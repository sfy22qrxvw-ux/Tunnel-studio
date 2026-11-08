# Copyright (c) 2026 Damian Gulczynski.
"""Physical asset categories for dedicated project workspaces."""
from pathlib import Path, PureWindowsPath
import os
import stat

CATEGORIES = {
    'imported': 'Clouds/Imported',
    'registered': 'Clouds/Registered',
    'derived': 'Clouds/Derived',
    'mesh': 'Meshes',
    'analysis': 'Analysis',
    'export': 'Exports',
    'gis': 'GIS/Shapefiles',
    'cad': 'CAD',
    'working': 'Working',
}


def is_link(path):
    """Includes Windows junctions/reparse points even on Python without is_junction."""
    info = os.lstat(path)
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def _category_path(root, kind):
    result = Path(root)
    for name in Path(CATEGORIES[kind]).parts:
        result = result/name
        result.mkdir(exist_ok=True)
        if is_link(result) or not result.is_dir():
            raise ValueError('Project data category must be a real folder: '+str(result))
    return result


def create_categories(root):
    for kind in CATEGORIES:
        _category_path(root, kind)


def folder(work, kind):
    root = Path(work)
    if not (root/'.tunnelstudio-workspace.json').is_file():
        return root
    from workspace_folders import managed_root
    if not managed_root(root):
        raise ValueError('Invalid managed project folder. Check its workspace identity.')
    return _category_path(root, kind)


def category_for_state_asset_map(state):
    """Return category keys for linked assets, retaining original path spellings.

    A cloud can also occur in review history. The specifically typed scene item
    takes precedence over aliases in those records. Virtual registration leaves
    the original cloud in its input class; merged geometry has its own class.
    """
    from projects import PATH_KEYS
    result = {}
    priority = {k:i for i,k in enumerate(('working','imported','analysis','mesh','registered','derived','cad','export','gis'))}
    sections = {'merge_source_revisions':'imported','cloud':'imported','surface_source':'imported','source_meta':'imported',
                'transformed':'registered','surface_result':'analysis','analysis_tool_result':'analysis',
                'free_section':'analysis','inspection_result':'analysis','design_comparison':'analysis',
                'export_ready':'export','gis_resources':'gis','cad':'cad','geometry':'cad'}
    def visit(value, context):
        if isinstance(value, list):
            for entry in value:
                if isinstance(entry, (dict,list)):
                    visit(entry,context)
            return
        if not isinstance(value, dict):
            return
        if value.get('registered_merge') or value.get('source_format')=='registered_merge':
            context='registered'
        elif value.get('cleaning_provenance') or value.get('sampled_from_mesh') or value.get('sampled_mesh_id'):
            context='derived'
        elif value.get('kind')=='mesh' or 'vertices_file' in value:
            context='mesh'
        elif value.get('kind')=='scan' and context=='working':
            context='imported'
        for key, entry in value.items():
            if key=='project_recovery':
                continue
            if key in PATH_KEYS and isinstance(entry,str) and entry:
                kind=context
                name=PureWindowsPath(entry).name.lower()
                if key=='derived' or name.endswith(('.analysis64','.surface_labels','.surface_ids','.inspection-results','.design-results','.large-section')):
                    kind='analysis'
                elif key in ('mask_file','before_mask','after_mask'):
                    kind='working'
                elif key in ('mesh_file','vertices_file','faces_file','face_flags_file','vertex_colours_file'):
                    kind='mesh'
                elif kind=='working' and name.endswith(('.xyz64','.attr32')):
                    kind='imported'
                if entry not in result or priority[kind]>priority[result[entry]]:
                    result[entry]=kind
            elif isinstance(entry,(dict,list)):
                visit(entry,sections.get(key,context))
    visit(state,'working')
    return result


def gis_member_names(state):
    """Map linked member paths to a safe dataset-relative filename on restore."""
    result={}
    for number,row in enumerate(state.get('gis_resources') or []):
        if not isinstance(row,dict):continue
        # The ordinal partitions datasets even when independent sources share a name.
        stem=Path(str(row.get('name') or 'Shapefile')).name
        def usable(text):
            return (isinstance(text,str) and text not in ('','.','..') and
                    not text.endswith((' ','.')) and not PureWindowsPath(text).is_reserved() and
                    not any(c in '<>:"/\\|?*' or ord(c)<32 for c in text))
        if not usable(stem):stem='Shapefile'
        family=f'{stem[:100]}-{number+1}'
        for member in row.get('members') or []:
            if not isinstance(member,dict):continue
            filename=member.get('name')
            source=member.get('file')
            if not isinstance(source,str) or not usable(filename):
                raise ValueError('Invalid archived shapefile member name.')
            result[source]=Path(family)/filename
    return result
