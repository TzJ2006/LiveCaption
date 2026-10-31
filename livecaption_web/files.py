"""File transcription using the same recognizer as live captions; never opens audio devices."""
import argparse
import datetime as dt
import json
import re
import wave
from pathlib import Path


def transcribe(path,model_dir):
    import numpy as np
    from .asr_worker import build_recognizer
    match=re.match(r'(\d{4}-\d{2}-\d{2})-(\d{2})(\d{2})(\d{2})',Path(path).name)
    if not match: raise ValueError('录音文件缺少可靠的采集日期与开始时间')
    start=dt.datetime.fromisoformat(match[1]+'T'+':'.join(match.groups()[1:]))
    recognizer=build_recognizer(Path(model_dir),2)
    stream=recognizer.create_stream(); result=[]
    with wave.open(str(path),'rb') as source:
        width=source.getsampwidth(); rate=source.getframerate(); channels=source.getnchannels(); offset=0
        if width not in (2,4): raise ValueError('仅支持 PCM16/PCM32 录音')
        while raw:=source.readframes(rate):
            samples=np.frombuffer(raw,dtype='<i2' if width==2 else '<i4').astype(np.float32)/(32768.0 if width==2 else 2147483648.0)
            samples=samples.reshape(-1,channels).mean(axis=1)
            stream.accept_waveform(rate,samples)
            while recognizer.is_ready(stream): recognizer.decode_stream(stream)
            offset+=len(samples)/rate
            if recognizer.is_endpoint(stream):
                text=str(recognizer.get_result(stream)).strip()
                if text: result.append({'at':(start+dt.timedelta(seconds=offset)).isoformat(),'text':text})
                recognizer.reset(stream)
        stream.input_finished()
        while recognizer.is_ready(stream): recognizer.decode_stream(stream)
        text=str(recognizer.get_result(stream)).strip()
        if text: result.append({'at':(start+dt.timedelta(seconds=offset)).isoformat(),'text':text})
    return result


def main():
    p=argparse.ArgumentParser(); p.add_argument('file',type=Path); p.add_argument('--model-dir',required=True,type=Path)
    a=p.parse_args(); print(json.dumps(transcribe(a.file,a.model_dir),ensure_ascii=False))


if __name__=='__main__': main()
