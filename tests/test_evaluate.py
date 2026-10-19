from karaoke.evaluate import reference_boundaries

HEAD = """[Script Info]
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,1,2,5,5,5,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def test_templater_source_lines_win_over_generated_ones(tmp_path):
    p = tmp_path / "fx.ass"
    p.write_text(HEAD + "Comment: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,karaoke,{\\k50}A{\\k50}B\n"
                 "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,fx,{\\k30}A\n", encoding="utf-8")
    assert reference_boundaries(p) == [(1.0, [1.0, 1.5])]


def test_nofx_files_use_plain_karaoke_dialogue(tmp_path):
    p = tmp_path / "nofx.ass"
    p.write_text(HEAD + "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,{\\k50}A{\\k50}B\n"
                 "Comment: 0,0:00:05.00,0:00:06.00,Default,,0,0,0,,{\\k50}C\n"
                 "Dialogue: 0,0:00:07.00,0:00:08.00,Default,,0,0,0,,plain sign\n", encoding="utf-8")
    assert reference_boundaries(p) == [(1.0, [1.0, 1.5])]
