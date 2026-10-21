"""Persistent visual targets and ambiguity-aware template matching."""
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


class TargetNotFound(RuntimeError):
    pass


class AmbiguousTarget(TargetNotFound):
    pass


@dataclass(frozen=True)
class VisualTarget:
    image: Path
    point: tuple[float, float]
    screen_size: tuple[int, int]
    threshold: float = .93
    margin: float = .035
    strategy: str = 'element'

    def __post_init__(self):
        object.__setattr__(self,'image',Path(self.image))
        if len(self.point)!=2 or len(self.screen_size)!=2 or not all(v>=4 for v in self.screen_size):
            raise ValueError('Target requires a click offset and reference screen dimensions')
        if not 0<self.threshold<=1 or not 0<=self.margin<1:
            raise ValueError('Invalid visual match thresholds')


@dataclass(frozen=True)
class Match:
    point: tuple[float, float]
    box: tuple[int, int, int, int]
    score: float
    scale: float


def locate(screen: Image.Image, target: VisualTarget) -> Match:
    with Image.open(target.image) as source:
        template = np.asarray(source.convert('RGB'))
    if min(template.shape[:2])<4 or float(template.astype(np.float32).std(axis=(0,1)).max())<3:
        raise TargetNotFound('Target has insufficient visual detail')
    if not (0<=target.point[0]<template.shape[1] and 0<=target.point[1]<template.shape[0]):
        raise ValueError('Target click offset is outside its template')
    frame = np.asarray(screen.convert('RGB'))
    ratio = min(screen.width/target.screen_size[0],screen.height/target.screen_size[1])
    scales = sorted({round(s,4) for s in (1.,ratio,ratio*.95,ratio*1.05) if .25<=s<=4})
    matches = []
    for scale in scales:
        width,height = round(template.shape[1]*scale),round(template.shape[0]*scale)
        if min(width,height)<4 or width>screen.width or height>screen.height:
            continue
        patch = cv2.resize(template,(width,height),interpolation=cv2.INTER_AREA if scale<1 else cv2.INTER_LINEAR)
        scores = cv2.matchTemplate(frame,patch,cv2.TM_CCOEFF_NORMED)
        scores = np.nan_to_num(scores,nan=-1.,posinf=-1.,neginf=-1.)
        for _ in range(2):
            _,score,_,(x,y) = cv2.minMaxLoc(scores)
            point = (x+target.point[0]*scale,y+target.point[1]*scale)
            matches.append(Match(point,(x,y,x+width,y+height),score,scale))
            radius = max(8,min(width,height)//4)
            scores[max(0,y-radius):y+radius+1,max(0,x-radius):x+radius+1] = -1
    matches.sort(key=lambda match:match.score,reverse=True)
    if not matches or matches[0].score<target.threshold:
        score = matches[0].score if matches else 0
        raise TargetNotFound(f'Target not visible (best score {score:.3f}, required {target.threshold:.3f})')
    best = matches[0]
    second = next((m for m in matches[1:] if max(abs(m.point[i]-best.point[i]) for i in (0,1))>10),None)
    if second and best.score-second.score<target.margin:
        raise AmbiguousTarget(f'Multiple matching targets ({best.score:.3f} vs {second.score:.3f})')
    return best
