# Prompt 配置

`prompts.yaml` 是项目唯一的 prompt 内容来源。生产代码不再读取独立 `.txt` prompt。

打开该 YAML 后可直接修改：

- `chat.characters.algernon.system_prompt`：Algernon 人物设定。
- `chat.characters.liya.system_prompt`：莉娅人物设定。
- `chat.characters.generated_default.system_prompt`：新增角色的统一初始人物设定。
- `chat.characters.cuige.system_prompt`：崔格人物设定。
- `chat.character_output_rules`：统一追加到所有角色 system prompt 末尾的声音、表情和情绪输出规则。
- `chat.user_prompt`：普通聊天当轮完整 user prompt 结构。
- `chat.contexts`：长期记忆和短期关系的包装文本与插入位置。
- `chat.current_relationship_tiers`：当前关系分对应的行为指导档位。
- `summaries.current_relationship_score`：每 5 轮关系评分的 system prompt 和 user prompt。
- `summaries`：其他总结调用的 system prompt 和 user prompt。
- `character_generation.expression_prompts`：新增角色的表情图片生成 prompt。
- `utility`、`tools`、`runtime`：MCP、图片、打断与错误信息等其他 prompt。

`{user_input}`、`{emomap_keys}`、`{recent_turns_json}` 等是程序在运行时填入的占位符。可修改它们周围的任何文字和顺序，但不要修改占位符名称。

聊天 user prompt、角色 system prompt、关系评分和其他总结 prompt 都会在下一次调用时读取新内容。当前关系分的行为指导会按角色分数动态加入 system prompt。
