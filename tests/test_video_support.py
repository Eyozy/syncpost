from api import services


class FakeResponse:
    ok = True


def test_native_video_uses_send_video_file_id():
    calls = []

    def telegram_request(method, payload):
        calls.append((method, payload))
        return FakeResponse()

    media = services.extract_media_payload(
        {"video": {"file_id": "video-1", "file_size": 100, "mime_type": "video/mp4"}}
    )

    services.publish_to_telegram_channel("caption", media, telegram_request, services.logging.getLogger())

    assert calls == [
        (
            "sendVideo",
            {
                "chat_id": services.TG_CHANNEL_ID,
                "video": "video-1",
                "caption": "caption",
                "parse_mode": "HTML",
                "supports_streaming": True,
            },
        )
    ]


def test_document_video_is_reuploaded_as_video(monkeypatch):
    calls = []

    class UploadResponse:
        ok = True

    monkeypatch.setattr(
        services,
        "download_media_file",
        lambda *args: {"content": b"video-bytes", "filename": "video.mp4"},
    )
    monkeypatch.setattr(
        "api.clients.req.post",
        lambda url, **kwargs: calls.append((url, kwargs)) or UploadResponse(),
    )

    media = services.extract_media_payload(
        {
            "document": {
                "file_id": "document-1",
                "file_size": 100,
                "mime_type": "video/mp4",
                "file_name": "video.mp4",
            }
        }
    )

    services.publish_to_telegram_channel("caption", media, lambda *_: None, services.logging.getLogger())

    assert media.source_kind == "video_document"
    assert calls[0][0].endswith("/sendVideo")
    assert "video" in calls[0][1]["files"]
    assert calls[0][1]["data"]["supports_streaming"] == "true"
