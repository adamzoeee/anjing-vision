"""Resample only connected, observed RGB point-map cells; never extrapolate."""
import numpy as np


def sample_cells(points, colors, valid, subdivisions=3, max_edge=.04):
    p=np.asarray(points,float);c=np.asarray(colors,float)
    v=np.asarray(valid,bool)&np.isfinite(p).all(-1)&np.isfinite(c).all(-1)
    good=v[:-1,:-1]&v[1:,:-1]&v[:-1,1:]&v[1:,1:]
    corners=[p[:-1,:-1],p[:-1,1:],p[1:,:-1],p[1:,1:]]
    for a,b in [(0,1),(0,2),(1,3),(2,3)]:
        good&=np.linalg.norm(corners[a]-corners[b],axis=-1)<=max_edge
    if not good.any():return np.empty((0,3)),np.empty((0,3))
    pc=[q[good] for q in corners]
    cc=[q[good] for q in [c[:-1,:-1],c[:-1,1:],c[1:,:-1],c[1:,1:]]]
    xyz=[];rgb=[]
    for y in (np.arange(subdivisions)+.5)/subdivisions:
        for x in (np.arange(subdivisions)+.5)/subdivisions:
            weights=[(1-x)*(1-y),x*(1-y),(1-x)*y,x*y]
            xyz.append(sum(w*q for w,q in zip(weights,pc)))
            rgb.append(sum(w*q for w,q in zip(weights,cc)))
    return np.vstack(xyz),np.vstack(rgb)
