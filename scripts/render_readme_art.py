#!/usr/bin/env python3
"""Графика Markdown: локальные SVG и GIF. Pillow нужен только для пересборки GIF."""
from html import escape
from pathlib import Path
import math

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/assets/readme'
W, H = 1200, 440


def svg_header(title, description, *, height=440):
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="{height}" viewBox="0 0 1200 {height}" role="img" aria-labelledby="title desc">
<title id="title">{escape(title)}</title><desc id="desc">{escape(description)}</desc>
<defs>
 <linearGradient id="bg" x2="1" y2="1"><stop stop-color="#0a1523"/><stop offset="1" stop-color="#15132f"/></linearGradient>
 <linearGradient id="edge" x2="1" y2="1"><stop stop-color="#c4fff1" stop-opacity=".7"/><stop offset=".42" stop-color="#e7faff" stop-opacity=".12"/><stop offset="1" stop-color="#c6b3ff" stop-opacity=".45"/></linearGradient>
 <linearGradient id="glass" x2="1" y2="1"><stop stop-color="#eafffa" stop-opacity=".12"/><stop offset="1" stop-color="#fff" stop-opacity=".025"/></linearGradient>
 <linearGradient id="accent" x2="1"><stop stop-color="#90f5d4"/><stop offset="1" stop-color="#bca7ff"/></linearGradient>
 <filter id="blur"><feGaussianBlur stdDeviation="52"/></filter>
 <clipPath id="clip"><rect width="1200" height="{height}" rx="24"/></clipPath>
</defs>
<g clip-path="url(#clip)"><rect width="1200" height="{height}" fill="url(#bg)"/>
<g filter="url(#blur)" opacity=".42"><ellipse cx="220" cy="-35" rx="260" ry="120" fill="#0cae9a"/><ellipse cx="1040" cy="{height}" rx="260" ry="180" fill="#7156d7"/></g>
'''


def text(x,y,value,size=18,color='#e8edf5',weight='400',spacing=None):
    extra=f' letter-spacing="{spacing}"' if spacing else ''
    return f'<text x="{x}" y="{y}" fill="{color}" font-family="Arial, Helvetica, sans-serif" font-size="{size}" font-weight="{weight}"{extra}>{escape(value)}</text>'


def hero_svg():
    s=svg_header('Lab Grader · Defense Queue','Очередь защиты лабораторных: допуск, посещение, приоритет от −1 и история.')
    s+='<rect x="20" y="20" width="1160" height="400" rx="22" fill="url(#glass)" stroke="url(#edge)"/>'
    s+=text(54,70,'LAB GRADER WEB / QUEUE MODULE',14,'#a5b7cc',spacing='2')
    s+=text(54,153,'LAB GRADER',68,'#f5f8fc','700')
    s+=text(54,212,'DEFENSE QUEUE',48,'#a1f3da','700')
    s+=text(56,266,'Защита лабораторных. В понятном порядке.',23)
    s+=text(56,299,'Допуск → посещение → защита → история',18,'#a8b9cc')
    for x,width,label in [(56,145,'ПРИОРИТЕТ −1'),(213,112,'SQLITE'),(337,184,'ЖИВАЯ ОЧЕРЕДЬ')]:
        s+=f'<rect x="{x}" y="342" width="{width}" height="34" rx="17" fill="#fff" fill-opacity=".055" stroke="#fff" stroke-opacity=".18"/>'
        s+=text(x+15,364,label,12,'#cad7e8')
    s+=text(782,71,'ПРИНЦИП ПРИОРИТЕТА',12,'#a5b7cc',spacing='2')
    for y,value,label,sub,accent in [(94,'−1','Просроченные работы','Общая нижняя граница','#a1f3da'),(180,'0','Текущий дедлайн','Срок на этой неделе','#eee6bc'),(266,'+1…','Будущие дедлайны','Чем ближе срок, тем раньше','#c5b6f7')]:
        s+=f'<rect x="758" y="{y}" width="388" height="74" rx="14" fill="url(#glass)" stroke="url(#edge)"/>'
        s+=f'<rect x="774" y="{y+15}" width="58" height="44" rx="10" fill="{accent}" fill-opacity=".13"/>'
        s+=text(785,y+45,value,24,accent,'700')+text(848,y+30,label,17)+text(848,y+53,sub,13,'#a5b7cc')
    s+=text(783,379,'max(−1, дедлайн − неделя занятия)',15,'#cbd4e8')
    return s+'</g></svg>\n'


def section_svg(filename,kicker,title,subtitle,tags):
    s=svg_header(title,subtitle,height=200)
    s+='<rect x="16" y="16" width="1168" height="168" rx="18" fill="url(#glass)" stroke="url(#edge)"/>'
    s+=text(44,53,kicker,12,'#a5b7cc',spacing='2')+text(44,107,title,40,'#f4f7fc','700')+text(46,147,subtitle,18,'#b5c3d7')
    for i,(label,value) in enumerate(tags):
        x=820+i*112
        s+=f'<rect x="{x}" y="50" width="100" height="100" rx="18" fill="url(#glass)" stroke="url(#edge)"/>'
        s+=text(x+16,95,value,28,'#a1f3da','700')+text(x+16,124,label,12,'#b8c7da')
    (OUT/filename).write_text(s+'</g></svg>\n',encoding='utf-8')


def badges_svg():
    s='<svg xmlns="http://www.w3.org/2000/svg" width="680" height="40" viewBox="0 0 680 40" role="img"><title>FastAPI · React + Vite · SQLite · Приоритет от −1</title>'
    for x,width,label,color in [(0,144,'FastAPI','#65d4b6'),(154,172,'React + Vite','#9abfff'),(336,134,'SQLite','#bba9f2'),(480,200,'Приоритет от −1','#7cdec4')]:
        s+=f'<rect x="{x+.5}" y=".5" width="{width-1}" height="35" rx="17" fill="#101b2b" stroke="#364557"/><circle cx="{x+18}" cy="18" r="3" fill="{color}"/>'
        s+=text(x+30,23,label,13,'#e5ecf7')
    return s+'</svg>\n'


def render_gif():
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    fontdir=Path('/System/Library/Fonts/Supplemental')
    regular=fontdir/'Arial.ttf'; bold=fontdir/'Arial Bold.ttf'
    # На другой ОС можно передать пути через окружение.
    import os
    regular=Path(os.getenv('README_FONT',str(regular)))
    bold=Path(os.getenv('README_FONT_BOLD',str(bold)))
    if not regular.exists() or not bold.exists():
        raise SystemExit('Для GIF задай README_FONT и README_FONT_BOLD на TTF с кириллицей')
    def font(size,b=False): return ImageFont.truetype(str(bold if b else regular),size)
    foreground=Image.new('RGBA',(W,H))
    d=ImageDraw.Draw(foreground)
    def t(x,y,value,size=18,color='#e8edf5',b=False): d.text((x,y),value,font=font(size,b),fill=color)
    d.rounded_rectangle((20,20,1180,420),radius=22,fill=(240,255,255,13),outline=(190,220,238,90))
    t(54,55,'LAB GRADER WEB / QUEUE MODULE',14,'#a5b7cc')
    t(54,91,'LAB GRADER',68,'#f5f8fc',True); t(54,161,'DEFENSE QUEUE',48,'#a1f3da',True)
    t(56,242,'Защита лабораторных. В понятном порядке.',23)
    t(56,279,'Допуск → посещение → защита → история',18,'#a8b9cc')
    for x,width,label in [(56,145,'ПРИОРИТЕТ −1'),(213,112,'SQLITE'),(337,184,'ЖИВАЯ ОЧЕРЕДЬ')]:
        d.rounded_rectangle((x,342,x+width,376),radius=17,fill=(240,255,255,12),outline=(230,245,255,45)); t(x+15,353,label,12,'#cad7e8')
    t(782,56,'ПРИНЦИП ПРИОРИТЕТА',12,'#a5b7cc')
    for y,value,label,sub,accent in [(94,'−1','Просроченные работы','Общая нижняя граница','#a1f3da'),(180,'0','Текущий дедлайн','Срок на этой неделе','#eee6bc'),(266,'+1…','Будущие дедлайны','Чем ближе срок, тем раньше','#c5b6f7')]:
        d.rounded_rectangle((758,y,1146,y+74),radius=14,fill=(230,245,255,18),outline=(200,220,245,70))
        d.rounded_rectangle((774,y+15,832,y+59),radius=10,fill=(180,220,230,20))
        t(785,y+23,value,24,accent,True); t(848,y+14,label,17); t(848,y+41,sub,13,'#a5b7cc')
    t(783,364,'max(−1, дедлайн − неделя занятия)',15,'#cbd4e8')
    frames=[]
    for i in range(40):
        phase=2*math.pi*i/40
        canvas=Image.new('RGBA',(W,H),'#0b1424')
        glow=Image.new('RGBA',(W,H)); g=ImageDraw.Draw(glow)
        x=180+90*math.sin(phase); y=-50+35*math.cos(phase)
        g.ellipse((x-260,y-130,x+260,y+130),fill=(16,155,141,125))
        x=1010+75*math.cos(phase); y=400+25*math.sin(phase)
        g.ellipse((x-240,y-150,x+240,y+150),fill=(100,69,183,135))
        g.line([(470+70*math.sin(phase),-30),(840+70*math.sin(phase),470)],fill=(115,177,216,35),width=80)
        canvas=Image.alpha_composite(canvas,glow.filter(ImageFilter.GaussianBlur(55)))
        canvas=Image.alpha_composite(canvas,foreground).convert('RGB')
        if i==0: canvas.save(OUT/'hero-preview.png',optimize=True)
        frames.append(canvas)
    # Общая палитра не мерцает при смене кадров; плавный цикл длится 4 секунды.
    samples=Image.new('RGB',(300,110*len(frames)))
    for i,frame in enumerate(frames): samples.paste(frame.resize((300,110)),(0,i*110))
    palette=samples.quantize(colors=256)
    frames=[frame.quantize(palette=palette,dither=Image.Dither.NONE) for frame in frames]
    frames[0].save(OUT/'hero.gif',save_all=True,append_images=frames[1:],duration=100,loop=0,optimize=True,disposal=1)


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'hero.svg').write_text(hero_svg(),encoding='utf-8')
    (OUT/'stack.svg').write_text(badges_svg(),encoding='utf-8')
    section_svg('research.svg','LAB GRADER / COMPUTATIONAL STUDY','Исследование очередей','Модели, распределения и проверяемые результаты.', [('студентов','30'),('работ','10'),('прогонов','10k')])
    section_svg('architecture.svg','LAB GRADER / ARCHITECTURE','Решения и основания','Приоритет. Хранение. Расписание.', [('решения','03'),('граница','−1'),('БД','SQL')])
    section_svg('queue.svg','LAB GRADER / DEFENSE QUEUE','Как устроена очередь','Допуск, посещение и попытка защиты.', [('состояния','03'),('граница','−1'),('история','LOG')])
    render_gif()
    print('\n'.join(f'{p.name}: {p.stat().st_size:,} bytes' for p in sorted(OUT.iterdir())))


if __name__=='__main__': main()
