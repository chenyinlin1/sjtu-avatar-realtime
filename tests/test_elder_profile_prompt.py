from handlers.agent.prompt.elder_profile_prompt import build_elder_profile_prompt


def test_build_elder_profile_prompt_with_usage_boundaries():
    prompt = build_elder_profile_prompt(
        {
            "elder_profile": {
                "nickname": " 王奶奶 ",
                "gender": "女",
                "age": 78,
                "native_place": "四川成都",
                "ignored": "不应出现",
            }
        }
    )

    assert "昵称：王奶奶" in prompt
    assert "性别：女" in prompt
    assert "年龄：78" in prompt
    assert "籍贯：四川成都" in prompt
    assert "仅按需参考" in prompt
    assert "实际称呼优先遵循角色设定" in prompt
    assert "非必要不使用称呼" in prompt
    assert "不要每次回复都称呼用户" in prompt
    assert "固定的‘昵称+回答’开头" in prompt
    assert "ignored" not in prompt
    assert "不应出现" not in prompt


def test_build_elder_profile_prompt_returns_empty_without_profile():
    assert build_elder_profile_prompt(None) == ""
    assert build_elder_profile_prompt({}) == ""
    assert build_elder_profile_prompt({"elder_profile": {}}) == ""
    assert build_elder_profile_prompt({"elder_profile": "invalid"}) == ""
