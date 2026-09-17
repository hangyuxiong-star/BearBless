from pathlib import Path

from PIL import Image

from bearbless.agent.grounding import SetOfMarkGrounder
from bearbless.runtime.commands import CommandResult


TSV = """level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext
5\t1\t1\t1\t1\t1\t20\t30\t40\t20\t90\t搜索
5\t1\t1\t1\t1\t2\t65\t30\t60\t20\t88\t歌曲
5\t1\t1\t1\t2\t1\t20\t90\t80\t24\t91\t推荐
"""


class Runner:
    def run(self, argv, **kwargs):
        assert "chi_sim+eng" in argv
        return CommandResult("ocr", tuple(argv), 0, TSV, "")


def test_grounder_groups_words_into_numbered_lines(tmp_path: Path):
    source = tmp_path / "frame.png"
    Image.new("RGB", (200, 160), "white").save(source)
    result = SetOfMarkGrounder(Runner()).ground(source)  # type: ignore[arg-type]
    assert [item.label for item in result.elements] == ["搜索 歌曲", "推荐"]
    assert result.elements[0].bounds == (20, 30, 125, 50)
    assert result.elements[0].center == (72, 40)
    assert result.annotated_path.exists()
