# Copyright (c) 2026 Damian Gulczynski.
"""Experimental SCV6 optical reconstruction, using recorded base parameters.

This is an independently derived ray model, not Leica's calibrated decoder.
Thermal/warm-up corrections and the full validity model remain unresolved.
Never use E57-fitted or scanner-specific constants in this implementation.
"""
import json
import re
import numpy as np

MODEL = 'rtc360_paraboloid_base_experimental_v1'
# Both observed metadata revisions use the same typed base-parameter schema.
# This permits metadata validation, not unvalidated binary data: describe() and
# split_scan() still enforce the SCV6 framing, raster and row-layout constraints.
SUPPORTED_API_VERSIONS = ('6.1.131', '6.4.204')
NOTICE = ('Experimental RTC360 reconstruction: factory calibration is not fully verified. '
          'Leica validity and double-scan filtering are not reproduced; retained readings '
          'may contain noise and overlap. Check against trusted survey data before measurement use.')


def retain_notice(output, source):
    """Cleaning/separating points cannot validate the source optical model."""
    if source.get('experimental_reconstruction'):
        output['experimental_reconstruction']=True
        output['warnings']=list(dict.fromkeys(list(output.get('warnings') or [])+[NOTICE]))
    return output


def require(ok, message):
    if not ok: raise ValueError(message)


def quantity(value, kind):
    require(isinstance(value, list) and len(value) == 2 and value[0] == kind,
            'Unsupported kinematic quantity type.')
    match = re.fullmatch(r'\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([A-Za-z]*)\s*', str(value[1]))
    require(match is not None, 'Unsupported kinematic quantity.')
    units = {'Angle': {'deg': np.pi/180, 'arcsec': np.pi/648000, 'rad': 1.},
             'Distance': {'m': 1., 'mm': .001, 'um': .000001}, 'float': {'': 1.}}
    require(match[2] in units[kind], 'Unsupported kinematic unit: '+match[2])
    result = float(match[1])*units[kind][match[2]]
    require(np.isfinite(result), 'Non-finite kinematic quantity.')
    return result


def parameters(setup):
    attrs = setup['attributes']
    version = attrs.get('ApiVersion')
    device = attrs.get('CreatorDevice')
    require(str(device or '').startswith('RTC360-'),
            f'Unsupported scanner identifier {device!r}; expected RTC360- followed by its serial number.')
    require(version in SUPPORTED_API_VERSIONS,
            f'Unsupported RTC360 API version {version!r}; supported metadata versions: '
            + ', '.join(SUPPORTED_API_VERSIONS) + '. Binary scan layout must also pass validation.')
    require(attrs.get('Status') == 'Completed', 'This scanner setup is not marked Completed.')
    k = setup.get('kinematic_parameters_unapplied')
    require(isinstance(k, dict), 'Missing scanner kinematic parameters.')
    try:
        p = {name: quantity(k[name]['Base'], 'Angle') for name in ('Lambda1','Lambda2','Mu','Epsilon','DeltaElevation')}
        p.update({name: quantity(k[name]['Base'], 'Distance') for name in ('Soy','ParaboloidRadius','RangeOffset')})
        p['RangeScale'] = quantity(k['RangeScale']['Base'], 'float')
        for name in ('Vertex','Laser'):
            p[name] = np.array([quantity(k[name]['Base'][axis], 'Distance') for axis in 'xyz'])
        tilt = setup['tilt_unapplied']
        require(tilt['Linear'][0] == 'Matrix3x3' and tilt['Translation'][0] == 'Vector3', 'Unsupported tilt representation.')
        R = np.array(json.loads(tilt['Linear'][1].replace('][', '],[')), dtype=float)
        t = np.array(json.loads(tilt['Translation'][1]), dtype=float)
    except (KeyError, TypeError, IndexError) as exc:
        raise ValueError('Incomplete scanner kinematic parameters or tilt.') from exc
    require(R.shape == (3,3) and np.isfinite(R).all() and np.allclose(R@R.T,np.eye(3),atol=1e-8)
            and abs(np.linalg.det(R)-1) < 1e-8 and t.shape == (3,) and np.isfinite(t).all()
            and np.max(abs(t)) < 1e-9, 'Unsupported scanner tilt transform.')
    # Bounds define this experimental model's supported mechanical family.
    require(abs(p['Mu']-np.pi/2)<.02 and abs(p['Epsilon']-np.pi/2)<.02
            and max(abs(p['Lambda1']),abs(p['Lambda2']),abs(p['DeltaElevation']))<.01
            and .08<p['ParaboloidRadius']<.10 and .99<p['RangeScale']<1.01
            and abs(p['RangeOffset'])<.01 and abs(p['Soy'])<.001
            and -.10<p['Laser'][0]<-.08 and np.max(abs(p['Laser'][1:]))<.001
            and -.10<p['Vertex'][0]<-.08 and -.05<p['Vertex'][1]<-.04
            and abs(p['Vertex'][2])<.001, 'Scanner geometry is outside the supported experimental model.')
    return p, R


def rotate_x(p, angle):
    x,y,z = np.broadcast_arrays(p[...,0],p[...,1],p[...,2])
    c,s = np.cos(angle),np.sin(angle)
    return np.stack([np.broadcast_to(x,c.shape),c*y-s*z,s*y+c*z],-1)


def reconstruct(azimuth, elevation, ranges, p):
    """Return scanner-frame XYZ for arrays of beams. No sampling/filtering."""
    a,b,r = (np.asarray(x,dtype=np.float64).reshape(-1) for x in (azimuth,elevation,ranges))
    require(a.shape == b.shape == r.shape and np.isfinite(a).all() and np.isfinite(b).all()
            and np.isfinite(r).all() and np.all(r>0), 'Invalid reconstruction inputs.')
    b = b+p['DeltaElevation']
    origin = rotate_x(p['Laser'],-b)
    beam = np.array([1.,p['Lambda1'],p['Lambda2']]);beam /= np.linalg.norm(beam)
    d = rotate_x(beam,-b)
    u = np.array([np.cos(p['Mu']),np.sin(p['Mu']),0.])
    v,R = p['Vertex'],p['ParaboloidRadius'];s = origin-v
    du,su = d@u,s@u
    A = np.sum(d*d,axis=1)-du*du
    B = 2*(np.sum(s*d,axis=1)-su*du-R*du)
    C = np.sum(s*s,axis=1)-su*su-2*R*su
    D = B*B-4*A*C
    require(np.all(A>1e-6) and np.all(D>=0), 'Unsupported laser/mirror intersection.')
    distance = (-B+np.sqrt(D))/(2*A)
    require(np.all((distance>0)&(distance<.5)), 'Unsupported internal optical path.')
    hit = origin+distance[:,None]*d
    normal = hit-v-((hit-v)@u+R)[:,None]*u
    normal /= np.linalg.norm(normal,axis=1)[:,None]
    outgoing = d-2*np.sum(d*normal,axis=1)[:,None]*normal
    hit,outgoing = rotate_x(hit,b),rotate_x(outgoing,b)
    corrected = r*p['RangeScale']+p['RangeOffset']-(distance+p['Laser'][0])
    q = hit+corrected[:,None]*outgoing
    e = p['Epsilon']-np.pi/2
    q = np.stack([np.cos(e)*q[:,0]+np.sin(e)*q[:,2], q[:,1]+p['Soy'],
                  -np.sin(e)*q[:,0]+np.cos(e)*q[:,2]],1)
    xyz = np.stack([np.cos(a)*q[:,0]+np.sin(a)*q[:,1],-np.sin(a)*q[:,0]+np.cos(a)*q[:,1],q[:,2]],1)
    require(np.isfinite(xyz).all(), 'Non-finite reconstructed coordinates.')
    return xyz
