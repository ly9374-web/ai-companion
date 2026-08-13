# Prompt 配置

`prompts.yaml` 是项目唯一的 prompt 内容来源。生产代码不再读取独立 `.txt` prompt。

打开该 YAML 后可直接修改：

- `chat.characters.algernon.system_prompt`：Algernon 完整 system prompt。
- `chat.characters.cuige.system_prompt`：崔格完整 system prompt。
- `chat.user_prompt`：普通聊天当轮完整 user prompt 结构。
- `chat.contexts`：长期记忆、长期关系和短期关系的包装文本与插入位置。
- `summaries`：三种总结调用各自完整的 system prompt 和 user prompt。
- `utility`、`tools`、`runtime`：MCP、图片、打断与错误信息等其他 prompt。

`{user_input}`、`{emomap_keys}`、`{recent_turns_json}` 等是程序在运行时填入的占位符。可修改它们周围的任何文字和顺序，但不要修改占位符名称。

聊天 user prompt、角色 system prompt 和三种总结 prompt 都会在下一次调用时读取新内容。角色 system prompt 发生变化时，当前会话的 agent 会在发起模型请求前自动更新。
