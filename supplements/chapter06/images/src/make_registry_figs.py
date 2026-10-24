"""第6章補足のAgent Registryの図を作る。Gateway補足と同じ部品（jinno-slides-color）を使う。"""
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


def dynamic_load():
    f = D(400, '承認はRegistryで進め、エージェントは質問のたびに承認済みのスキルを読み込む')
    # 左：手順を管理するチーム
    f.cicon(130, 40, 'user', '手順を管理するチーム', 70)
    f.carrow(270, 110, 390, 110, 'a', '登録・承認')
    # 中央：Registry
    f.rect(400, 20, 370, 260)
    f.icon(460, 36, 'aws-agentcore', size=46)
    f.txt(500, 70, 'Agent Registry', 25, True)
    f.chip(430, 110, 310, 'SKILL 1.0.0　承認済み', 'a')
    f.chip(430, 156, 310, 'SKILL 1.1.0　審査中', 'p')
    f.txt(430, 230, '検索APIが返すのは\n承認済みのバージョンだけ', 20)
    # 右：エージェント
    f.cicon(1040, 30, 'agent', '問い合わせ対応Agent', 64)
    f.carrow(930, 170, 780, 170, 'a', '① 質問のたびに検索')
    f.carrow(780, 236, 930, 236, 'a', '② SKILL.md')
    f.tbox(940, 200, 200, 80, 'AgentSkillsに\n渡して回答', 21, 'a')
    f.tbox(20, 310, 1120, 64, 'スキルを直して承認すれば、エージェントのコードを変えずに次の質問から新しい手順になる', 23, 'g')
    f.save(OUT / 'agent-registry-dynamic-load.svg')


def version_switch():
    f = D(380, '次のバージョンの審査中も、エージェントは承認済みのバージョンを読み込む')
    # askの中身：手元のPCでStrandsのエージェントを動かす
    f.rect(10, 10, 1140, 96)
    f.cicon(70, 22, 'agent', size=54)
    f.txt(120, 50, 'ask：手元のPCでStrands Agentsのエージェントを動かす', 23, True)
    f.txt(120, 86, 'Registryで承認済みのスキルを検索 → 見つかればAgentSkillsに渡す → Bedrockのモデルで回答', 20)
    steps = [
        ('setup', '3件を下書きで登録', 'スキルなし', 'o'),
        ('approve', '1.0.0を承認', '1.0.0', 'a'),
        ('revise', '1.1.0を下書きで作成', '1.0.0のまま', 'a'),
        ('approve', '1.1.0を承認', '1.1.0', 'g'),
    ]
    w, gap, top = 262, 24, 130
    for i, (cmd, what, loads, tone) in enumerate(steps):
        x = 10 + i * (w + gap)
        f.card(x, top, w, 100, cmd, what, None)
        f.txt(x + 18, top + 146, 'askで読み込むバージョン', 20)
        f.tbox(x, top + 164, w, 62, loads, 26, tone)
        if i < 3:
            f.carrow(x + w + 2, top + 50, x + w + gap - 2, top + 50, 'a')
    f.save(OUT / 'agent-registry-version-switch.svg')


def mcp_client():
    f = D(330, 'Claude CodeからmcpプロキシでRegistryのMCPエンドポイントへつなぐ')
    f.cicon(70, 30, 'user', '開発者', 64)
    f.carrow(125, 80, 205, 80, 'a')
    f.rect(215, 20, 320, 120)
    f.cicon(265, 32, 'agent', size=50)
    f.txt(305, 68, 'Claude Code', 24, True)
    f.txt(235, 116, '「障害対応のスキルはある？」', 18)
    f.carrow(540, 80, 590, 80, 'a')
    f.tbox(600, 40, 230, 80, 'mcp-proxy-for-aws\nIAMの署名を付ける', 21, 'p')
    f.carrow(835, 80, 885, 80, 'a')
    f.rect(895, 20, 255, 120)
    f.icon(940, 36, 'aws-agentcore', size=46)
    f.txt(976, 70, 'Registry', 24, True)
    f.txt(915, 118, 'MCPエンドポイント', 20)
    f.txt(20, 200, 'MCPツールとして使えるAPI', 22, True)
    for i, name in enumerate(['search（検索）', 'list（一覧）', 'batch_get（本文の取得）']):
        f.chip(20 + i * 380, 222, 350, name, 'a', 40)
    f.txt(20, 304, '返るのは承認済みのレコードだけ。呼び出す人のIAMにInvokeRegistryMcpなどの許可が必要', 20)
    f.save(OUT / 'agent-registry-mcp-client.svg')


def org_autodetect():
    f = D(420, '各アカウントのRuntimeとGatewayを自動で集め、補って承認してから共有する')
    for i, team in enumerate(['サポートチームのアカウント', '営業チームのアカウント']):
        y = 20 + i * 150
        f.rect(10, y, 330, 130, dash=True)
        f.txt(28, y + 32, team, 21, True)
        f.chip(28, y + 56, 140, 'Runtime', 'a', 36)
        f.chip(182, y + 56, 140, 'Gateway', 'a', 36)
    f.icon(400, 70, 'aws-organizations', size=44)
    f.carrow(350, 180, 450, 180, 'a', '自動検出')
    f.rect(460, 20, 330, 280)
    f.icon(510, 36, 'aws-agentcore', size=46)
    f.txt(550, 70, '管理用のRegistry', 23, True)
    f.chip(480, 104, 290, '下書きで登録される', 'p', 38)
    f.txt(480, 176, '所有者・用途・入出力・', 20)
    f.txt(480, 204, '認証方式を補って承認', 20)
    f.chip(480, 236, 290, '承認済み', 'g', 38)
    f.carrow(800, 160, 900, 160, 'a', 'RAMで共有')
    f.cicon(1020, 60, 'agent', 'ほかのチーム', 64)
    f.txt(1020, 230, '検索して再利用', 21, False, 'middle')
    f.tbox(10, 320, 1140, 76, '自動検出は同じリージョンのRuntimeとGatewayが対象。独自の環境のエージェントや\n配布しているスキルは、手動やCI/CDから登録する', 21, None)
    f.save(OUT / 'agent-registry-org-autodetect.svg')


if __name__ == '__main__':
    dynamic_load()
    version_switch()
    mcp_client()
    org_autodetect()
