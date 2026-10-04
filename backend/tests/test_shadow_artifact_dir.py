from __future__ import annotations

from unittest import mock

from app.core.config import settings
from app.services.ml import shadow
from app.services.utils import BACKEND_ROOT, MEDIA_ROOT


def test_relative_model_dir_lands_in_the_media_volume():
    # Production mounts the persistent volume at BACKEND_ROOT/media; resolving under
    # the app package put trained models in the container layer, lost on redeploy.
    with mock.patch.object(settings, "ml_shadow_model_dir", "media/models/shadow"):
        assert shadow._artifact_dir() == (MEDIA_ROOT / "models" / "shadow").resolve()
        assert shadow._artifact_dir().parent.parent == (BACKEND_ROOT / "media").resolve()
