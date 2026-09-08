"""Verify whether separate window passes admit a reliable local registration."""
import cv2
import numpy as np
from audit_scan46_observation_planes import load_inputs
maps,images,R,t=load_inputs()
sift=cv2.SIFT_create(contrastThreshold=.01)
mask=np.zeros((224,224),np.uint8)
for a,b,c,d in [(25,48,18,131),(47,196,18,35),(45,220,113,140)]:mask[a:b,c:d]=255
k,d=sift.detectAndCompute(cv2.cvtColor(np.array(images[145],np.uint8),cv2.COLOR_RGB2GRAY),mask)
for ref in [680,687,695,875]:
    k2,d2=sift.detectAndCompute(cv2.cvtColor(np.array(images[ref],np.uint8),cv2.COLOR_RGB2GRAY),None)
    matches=[a for a,b in cv2.BFMatcher().knnMatch(d,d2,k=2) if a.distance<.7*b.distance]
    print(ref,'opaque reference features',len(k),'matches',len(matches))
    if len(matches)>=4:
        a=np.float32([k[m.queryIdx].pt for m in matches]);b=np.float32([k2[m.trainIdx].pt for m in matches])
        h,good=cv2.findHomography(a,b,cv2.RANSAC,2.)
        print('homography inliers',int(good.sum()) if good is not None else 0)
