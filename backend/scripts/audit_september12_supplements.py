"""Inspect the newly supplied clips; never change the accepted scan."""
import argparse
from pathlib import Path
import json
import cv2
import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/work/46/local_gaps_20260912'
OUT.mkdir(exist_ok=True)


def main(videos: Path) -> None:
    if not videos.is_dir():
        raise FileNotFoundError(f'补拍视频目录不存在: {videos}')
    for label, name in [('shelf','书架上面.mp4'),('window','窗户边角.mp4'),('bedwall','床头墙/1000044793.mp4'),('orange','橙色书架/1000044794.mp4'),('oldwindow1','窗户那面墙/1000045072.mp4'),('oldwindow2','窗户那面墙/窗户那面.mp4')]:
        cap=cv2.VideoCapture(str(videos/name))
        fps=cap.get(cv2.CAP_PROP_FPS); count=int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        canvas=Image.new('RGB',(1200,900)); draw=ImageDraw.Draw(canvas); rows=[]
        for i,frame in enumerate(np.linspace(0,count-1,12).astype(int)):
            cap.set(cv2.CAP_PROP_POS_FRAMES,int(frame)); ok,bgr=cap.read()
            if not ok:continue
            rgb=cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB)
            im=Image.fromarray(rgb); im.thumbnail((300,270))
            x=(i%4)*300;y=(i//4)*300;canvas.paste(im,(x,y+25))
            draw.text((x+5,y+5),f'{label} {frame/fps:.2f}s frame {frame}',fill='white')
            rows.append({'frame':int(frame),'seconds':frame/fps,'saturated_fraction':float((rgb.min(-1)>248).mean()),'sharpness':float(cv2.Laplacian(cv2.cvtColor(bgr,cv2.COLOR_BGR2GRAY),cv2.CV_64F).var())})
        canvas.save(OUT/(label+'_contact.png'))
        (OUT/(label+'_frames.json')).write_text(json.dumps({'video':str(name).replace('\\','/'),'frames':count,'fps':fps,'samples':rows},indent=2,ensure_ascii=False),encoding='utf-8')
        print(label,count,fps,flush=True)
        cap.release()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--videos',
        type=Path,
        default=ROOT / 'data/training_sources/scan46_buchong2',
        help='第四个研究增量包解压后的 buchong2 视频目录',
    )
    main(parser.parse_args().videos)
