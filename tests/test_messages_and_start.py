from api import index


def test_handle_start_command_uses_shared_welcome_text(monkeypatch):
    sent = []

    monkeypatch.setattr(index, 'is_config_complete', lambda: True)
    monkeypatch.setattr(index, 'send_tg_message', lambda chat_id, text, reply_to=None: sent.append((chat_id, text, reply_to)))

    index.handle_start_command(123)

    assert sent == [(123, index.WELCOME_TEXT, None)]


def test_handle_check_config_callback_uses_shared_welcome_text(monkeypatch):
    edited = []
    answered = []

    monkeypatch.setattr(index, 'is_config_complete', lambda: True)
    monkeypatch.setattr(index, 'edit_message_text', lambda chat_id, message_id, text: edited.append((chat_id, message_id, text)) or True)
    monkeypatch.setattr(index, 'answer_callback_query', lambda callback_query_id, text=None, show_alert=False: answered.append((callback_query_id, text, show_alert)) or True)

    index.handle_check_config_callback({
        'from': {'id': 123},
        'message': {'message_id': 456},
        'id': 'cb-1',
    })

    assert edited == [(123, 456, f'✅ <b>配置检测通过！</b>\n\n{index.WELCOME_TEXT}')]
    assert answered == [('cb-1', '配置检测通过！', False)]
