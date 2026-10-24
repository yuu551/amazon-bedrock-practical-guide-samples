"""第1章補足のeffortの図を作る。Gateway補足と同じ部品（jinno-slides-color）を使う。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / '.codex/skills/jinno-slides-color/scripts'))
from jinno_color import ColorFig, tones

OUT = Path(__file__).resolve().parent.parent
ACC = '#28677d'


class D(ColorFig):
    palette = tones(ACC)

    def __init__(self, h, title):
        super().__init__(1160, h, title=title)


def levels():
    f = D(390, 'effortを上げるほどエージェントがよく考え、待ち時間と出力の料金も増える')
    names = ['low', 'medium', 'high', 'xhigh', 'max']
    moods = ['さっと答える', '少し考える', 'しっかり考える', 'かなり考える', 'とことん考える']
    w, gap = 200, 30
    f.txt(20, 40, 'thinkingの量・待ち時間・出力の料金', 23, True)
    f.carrow(470, 32, 1140, 32, 'o', '')
    f.ctxt(1140, 64, '段階を上げるほど増える', 'o', 20, False, 'end')
    for i, (name, mood) in enumerate(zip(names, moods)):
        x = 20 + i * (w + gap)
        cx = x + w / 2
        tone = 'a' if i < 3 else 'p'
        # エージェントと、考えている量を表す吹き出しの玉
        f.cicon(cx - 30, 120, 'agent', size=60)
        for k in range(i + 1):
            f.ring(cx + 6 + k * 9, 132 - k * 8, 4 + k * 1.6, tone)
        f.tbox(x, 212, w, 56, name, 26, tone)
        f.txt(cx, 300, mood, 21, False, 'middle')
        if i < 4:
            f.carrow(x + w + 4, 240, x + w + gap - 4, 240, 'a')
    for i, lines in ((1, ['Opus 5.5・Haiku 5.5', 'の既定']), (2, ['Sonnet 5.5', 'の既定'])):
        for j, line in enumerate(lines):
            f.ctxt(20 + i * (w + gap) + w / 2, 340 + j * 28, line, 'a', 21, True, 'middle')
    f.save(OUT / 'effort-levels.svg')


def output_limit():
    f = D(300, '出力上限にはthinkingの分も含まれる')
    f.txt(20, 40, '出力上限（maxTokens）', 23, True)
    f.rect(20, 60, 1120, 70)
    f.tbox(20, 60, 640, 70, 'thinking', 24, 'p')
    f.tbox(660, 60, 480, 70, '利用者に表示する回答', 24, 'a')
    f.txt(20, 180, '上限を小さくしすぎると', 23, True)
    f.rect(20, 200, 600, 70)
    f.tbox(20, 200, 420, 70, 'thinking', 24, 'p')
    f.tbox(440, 200, 180, 70, '回答の途中', 22, 'a')
    f.ncross(650, 235)
    f.ctxt(680, 244, '回答を書き終える前に上限に達する', 'o', 22, True)
    f.save(OUT / 'effort-output-limit.svg')


if __name__ == '__main__':
    levels()
    output_limit()
