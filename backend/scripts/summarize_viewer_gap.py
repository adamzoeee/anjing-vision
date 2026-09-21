"""Classify actual WebGL depth coverage, not RGB-background guesses."""
import json
import numpy as np
from PIL import Image, ImageDraw
from viewer_gap_diagnostic import OUT

POLYS={
 'front':{'R1':[(778,445),(889,445),(883,622),(773,670)],'R2':[(610,676),(781,674),(833,737),(810,795),(670,786),(616,733)],'R3':[(273,727),(610,728),(668,777),(330,787),(273,761)],'R4':[(543,265),(606,265),(605,621),(535,633)]},
 'oblique':{'R1':[(678,445),(765,467),(731,585),(679,633)],'R2':[(571,634),(679,622),(719,702),(665,766),(569,741)],'R3':[(278,718),(564,700),(605,748),(337,791)],'R4':[(522,272),(594,275),(556,639),(506,648)]}}

def depth(name):
    rgb=np.asarray(Image.open(OUT/(name+'_depth.png')),dtype=np.uint32)
    z=(rgb[:,:,0]*65536+rgb[:,:,1]*256+rgb[:,:,2])/10000.
    return np.where(z>0,z,np.inf)

def main():
    report={}
    for view,polys in POLYS.items():
        b,f,c,a,d=[depth(view+'_'+n) for n in ['baseline','formal','r2','combined','density120']]
        rgb=np.asarray(Image.open(OUT/(view+'_formal.png')))[:,:,:3]
        report[view]={}
        heat=np.zeros((896,896,3),np.uint8)
        heat[:]=[40,40,40]
        for region,poly in polys.items():
            im=Image.new('1',(896,896));ImageDraw.Draw(im).polygon(poly,fill=1);mask=np.asarray(im)
            empty=mask&~np.isfinite(f)
            has_candidate=empty&np.isfinite(c)
            dark=mask&np.isfinite(f)&(rgb.max(-1)<40)
            blocked=mask&np.isfinite(c)&np.isfinite(f)&(c>f+.002)
            heat[empty]=[240,70,60];heat[has_candidate]=[50,200,110];heat[dark]=[200,100,240];heat[blocked]=[80,130,240]
            report[view][region]={
                'pixels':int(mask.sum()),'no_rendered_depth':int(empty.sum()),
                'rendered_but_dark':int(dark.sum()),
                'candidate_covers_empty_depth':int(has_candidate.sum()),
                'candidate_behind_current_surface_not_proof_of_wrong_occlusion':int(blocked.sum()),
                'visual_layer_covers_baseline_empty':int((mask&~np.isfinite(b)&np.isfinite(f)).sum()),
                'size_120_fills_empty':int((empty&np.isfinite(d)).sum())}
        Image.fromarray(heat).save(OUT/(view+'_depth_classification.png'))
    for view in ['front','oblique','overview','overhead']:
        page=Image.new('RGB',(1792,936),(16,20,27));page.paste(Image.open(OUT/(view+'_formal.png')),(0,40));page.paste(Image.open(OUT/(view+'_combined.png')),(896,40))
        draw=ImageDraw.Draw(page);draw.text((12,12),'FORMAL / ACTUAL WEBGL',fill='white');draw.text((908,12),'EXISTING R2 / ACTUAL WEBGL - NOT ACCEPTED',fill='white');page.save(OUT/(view+'_comparison.png'))
        page.paste(Image.open(OUT/(view+'_local_repair.png')),(896,40))
        draw.rectangle((896,0,1792,39),fill=(16,20,27))
        draw.text((908,12),'LOCAL CANDIDATE / PARTIAL - NOT PUBLISHED',fill='white')
        page.save(OUT/(view+'_local_comparison.png'))
    (OUT/'depth_audit.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
