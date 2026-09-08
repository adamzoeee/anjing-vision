"""Inspect cached window supplement views without extracting new video frames."""
import numpy as np
from PIL import Image,ImageDraw
from audit_scan46_observation_planes import ROOT
p=ROOT/'data/work/46/supplement_buchong2_clip2/slam3r/scene/preds/input_imgs.npy'
images=np.load(p,mmap_mode='r')
sheet=Image.new('RGB',(6*224,3*250))
for k,i in enumerate(range(15,96,5)):
    im=Image.fromarray(np.asarray(images[i],np.uint8));sheet.paste(im,((k%6)*224,(k//6)*250+25))
    ImageDraw.Draw(sheet).text(((k%6)*224+5,(k//6)*250+5),str(i),fill='white')
sheet.save(ROOT/'data/work/46/window_components_20260907/supplement_sheet.png')
images=np.load(ROOT/'data/work/45/slam3r/scene/preds/input_imgs.npy',mmap_mode='r')
sheet=Image.new('RGB',(6*224,3*250))
for k,i in enumerate([880,882,884,886,888,890,891,892,893,894,895,896,897,898,899]):
    sheet.paste(Image.fromarray(np.asarray(images[i],np.uint8)),((k%6)*224,(k//6)*250+25))
    ImageDraw.Draw(sheet).text(((k%6)*224+5,(k//6)*250+5),str(i),fill='white')
sheet.save(ROOT/'data/work/46/window_components_20260907/main_passes.png')
