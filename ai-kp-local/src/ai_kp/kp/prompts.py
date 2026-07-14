KP_SYSTEM_PROMPT = """你是本地跑团平台中的 AI KP。
你的职责是推进场景、扮演 NPC、要求检定、维护公平性，并尊重已知事实。
不要泄露玩家角色不可见的秘密信息。
当信息不足时，给出可行动的选择，而不是假装已经知道。
"""


def build_turn_prompt(player_action: str, memories: list[str], npc_candidates: list[str]) -> str:
    memory_block = "\n".join(f"- {item}" for item in memories) or "- 无相关长期记忆"
    npc_block = "\n".join(f"- {item}" for item in npc_candidates) or "- 无可自然出现的旧 NPC"
    return f"""玩家行动：
{player_action}

相关记忆：
{memory_block}

可能自然出现的旧 NPC：
{npc_block}

请输出：
1. 场景反馈
2. 是否需要检定
3. 世界状态变化
4. 需要写入长期记忆的候选事实
"""

