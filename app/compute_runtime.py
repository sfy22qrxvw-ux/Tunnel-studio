"""Bounded CPU concurrency for independent native numerical work.

Jobs that mutate the workspace remain serial. Read-only tiles may run together
with bounded pending work; callers commit results in deterministic order. CPU
threads are not GPU acceleration, and worker counts are not speed guarantees.
"""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import os
import sys


class ProcessingCancelled(ValueError):
    """A cooperative stop at a complete numerical chunk boundary."""


def cancelled():
    """Use an existing job context without importing the application server."""
    jobs = sys.modules.get('job_api')
    surfaces = sys.modules.get('surface_api')
    server = sys.modules.get('server')
    event = getattr(jobs, 'CANCEL', None)
    surface_event = getattr(surfaces, 'CANCEL', None)
    path = getattr(server, 'JOB_CANCEL_FILE', None)
    return bool((event is not None and event.is_set()) or
                (surface_event is not None and surface_event.is_set()) or
                (path is not None and path.exists()))


def check_cancel(cancel=None):
    if (cancel or cancelled)():
        raise ProcessingCancelled('Operation cancelled. Previous results are unchanged.')


def memory_budget():
    available = 2 * 1024**3
    try:
        import psutil
        available = int(psutil.virtual_memory().available)
    except Exception:
        # Resource probes are advisory. Restricted hosts can raise psutil's
        # own AccessDenied/NoSuchProcess errors, outside OSError's hierarchy.
        pass
    return max(128*1024**2, min(7*1024**3, int(available*.55)))


def workers(memory_per_worker=128*1024**2, maximum=8):
    cores = os.cpu_count() or 1
    try:
        import psutil
        affinity = psutil.Process().cpu_affinity()
        if affinity:cores = min(cores, len(affinity))
    except Exception:
        pass
    requested = os.environ.get('TUNNELSTUDIO_WORKERS')
    if requested:
        try:cores = min(cores, max(1, int(requested)))
        except ValueError:pass
    return max(1, min(int(maximum), cores, memory_budget()//max(1, int(memory_per_worker))))


def ordered_map(function, items, worker_count=None, cancel=lambda: False, on_complete=None):
    """At most one submitted item per worker, returned in input order.

Inputs are consumed lazily. Native work already in progress finishes its bounded
tile before shutdown; cancellation never commits a later tile ahead of an
earlier one. Workers must not mutate app state or share output file handles.
"""
    count = workers() if worker_count is None else max(1, int(worker_count))
    if count == 1:
        for done, item in enumerate(items, 1):
            check_cancel(cancel)
            value = function(item)
            check_cancel(cancel)
            if on_complete:on_complete(done)
            yield value
        return
    iterator = iter(items)
    pool = ThreadPoolExecutor(max_workers=count, thread_name_prefix='TunnelStudioCompute')
    pending, ready = {}, {}
    submitted = emitted = completed = 0

    def submit():
        nonlocal submitted
        check_cancel(cancel)
        try:item = next(iterator)
        except StopIteration:return False
        check_cancel(cancel)
        pending[pool.submit(function, item)] = submitted
        submitted += 1
        return True

    try:
        for _ in range(count):
            if not submit():break
        while pending or ready:
            check_cancel(cancel)
            if emitted not in ready:
                done, _ = wait(pending, timeout=.1, return_when=FIRST_COMPLETED)
                for future in done:
                    ready[pending.pop(future)] = future.result()
                    completed += 1
                    if on_complete:on_complete(completed)
                continue
            value = ready.pop(emitted)
            emitted += 1
            yield value
            # Includes completed-but-not-yet-written results in the bound.
            submit()
    finally:
        for future in pending:future.cancel()
        pool.shutdown(wait=True, cancel_futures=True)
        if hasattr(iterator, 'close'):iterator.close()


def process_chunks(function, chunks, *, total=None, worker_count=None,
                   memory_per_worker=128*1024**2, progress=lambda _: None,
                   label='Processing', cancel=None):
    """Map independent, owned chunks with bounded memory and ordered output.

    Does not split, resize or materialise the input stream. Known workloads of
    fewer than four chunks run on the calling thread. For unknown streams the
    first three chunks are completed serially, without lookahead or buffering;
    any remaining chunks pass directly to ordered_map. Only the coordinator reports completion;
    workers must not mutate shared state or write shared files. Cancellation
    drops queued results and joins running chunks; Python cannot kill threads
    in the middle of a native calculation. Close this generator on early exit.
    """
    cancel = cancelled if cancel is None else cancel
    check_cancel(cancel)
    count = workers(memory_per_worker) if worker_count is None else max(1, int(worker_count))
    if total is None:
        try:total = len(chunks)
        except TypeError:pass
    iterator = iter(chunks)
    if total is not None:
        if total < 4:count = 1
        else:count = min(count, total)

    def report(done):
        if total:
            progress(f'{label}: {min(100, int(100*done/total))}% · {done:,}/{total:,} chunks done')
        else:
            progress(f'{label}: {done:,} chunks done')

    results = None
    try:
        initial_done = 0
        if total is None and count > 1:
            for _ in range(3):
                check_cancel(cancel)
                try:item = next(iterator)
                except StopIteration:return
                value = function(item)
                check_cancel(cancel)
                initial_done += 1
                report(initial_done)
                yield value
                # Do not retain a preceding owned chunk while requesting another.
                del item, value
        results = ordered_map(function, iterator, count, cancel,
                              lambda done: report(initial_done + done))
        yield from results
    finally:
        if results is not None:results.close()
        if hasattr(iterator, 'close'):iterator.close()
