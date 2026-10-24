"""第4章補足のDogwoodの図を作る。Gateway解説資料と同じ部品（jinno-slides-color）を使う。"""
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

    def node(self, x, w, kind, label, aws=False):
        self.rect(x, 8, w, 104)
        if aws:
            self.icon(x + w / 2, 18, kind, size=50)
        else:
            self.cicon(x + w / 2, 16, kind, size=54)
        self.txt(x + w / 2, 96, label, 24, True, 'middle')


def sequence():
    f = D(470, '同じポリシーセッションで、検索を済ませた後の起票だけを許可する')
    f.node(20, 220, 'agent', 'Agent')
    f.node(470, 220, 'aws-agentcore', 'Gateway', aws=True)
    f.node(920, 220, 'service', 'AtlassianOps')
    for x in (130, 580, 1030):
        f.path(f'M{x} 120L{x} 400', dash=True)
    # ① 検索前の起票は止める
    f.arrow(140, 172, 570, 172, '① create_jira_ticket（検索前）')
    f.ncross(600, 172)
    f.ctxt(626, 180, '拒否：検索の履歴がない', 'o', 23, True)
    # ② 検索は通し、履歴に残る
    f.arrow(140, 252, 570, 252, '② confluence_search')
    f.arrow(590, 252, 1020, 252, '許可して転送')
    f.txt(600, 296, '結果を返し、セッションの履歴に記録', 21)
    # ③ 30分以内の起票は通す
    f.arrow(140, 352, 570, 352, '③ create_jira_ticket（30分以内）')
    f.arrow(590, 352, 1020, 352, '許可して起票')
    f.tbox(20, 416, 1120, 46, 'x-amzn-bedrock-agentcore-policy-session-id は3回とも同じ値', 21, None)
    f.save(OUT / 'gateway-dogwood-sequence.svg')


def credit():
    f = D(380, 'クレジットの確認で返された値と、適用時の指定を照合する')
    f.card(10, 10, 430, 270, 'クレジット確認の応答', '', 'a')
    f.card(720, 10, 430, 270, 'クレジット適用の指定', '', None)
    rows = [('顧客ID', 'C-001', 'C-001', True), ('対象月', '2026年4月', '2026年4月', True), ('金額', '4,000円', '40,000円', False)]
    for i, (k, left, right, ok) in enumerate(rows):
        y = 112 + i * 58
        f.txt(40, y, f'{k}：{left}', 25)
        f.txt(750, y, f'{k}：{right}', 25, not ok)
        f.carrow(460, y - 8, 700, y - 8, 'g' if ok else 'o')
        f.ctxt(580, y - 22, '一致' if ok else '不一致', 'g' if ok else 'o', 22, True, 'middle')
    f.tbox(10, 304, 1140, 64, '金額が一致しないので、Gatewayがクレジットの適用を拒否する', 24, 'o')
    f.save(OUT / 'gateway-dogwood-credit.svg')


if __name__ == '__main__':
    sequence()
    credit()
