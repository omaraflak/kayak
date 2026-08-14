"""Tests for the conversation workspace endpoints and their pure helpers.

The filesystem view and uploads take untrusted input -- paths from the browser,
filenames from folder uploads -- so the confinement and preview logic is what
these tests pin down. The terminal itself is a raw PTY bridge with no parsing
of its own.
"""

import pytest

from backend.app.routes.workspace import (
    preview_disposition,
    safe_upload_path,
)


class TestSafeUploadPath:
    def test_a_plain_filename_is_kept(self):
        assert str(safe_upload_path("report.pdf")) == "report.pdf"

    def test_a_folder_upload_keeps_its_structure(self):
        assert str(safe_upload_path("project/src/main.py")) == "project/src/main.py"

    def test_windows_separators_are_normalised(self):
        assert str(safe_upload_path("project\\src\\main.py")) == "project/src/main.py"

    def test_current_directory_segments_are_dropped(self):
        assert str(safe_upload_path("./project/./file.txt")) == "project/file.txt"

    def test_parent_references_are_rejected_not_rewritten(self):
        with pytest.raises(ValueError):
            safe_upload_path("../outside.txt")
        with pytest.raises(ValueError):
            safe_upload_path("project/../../outside.txt")

    def test_absolute_paths_are_rejected(self):
        with pytest.raises(ValueError):
            safe_upload_path("/etc/passwd")

    def test_an_empty_name_is_rejected(self):
        with pytest.raises(ValueError):
            safe_upload_path("")
        with pytest.raises(ValueError):
            safe_upload_path("./.")


class TestPreviewDisposition:
    def test_images_and_pdfs_render_inline(self):
        assert preview_disposition("plot.png") == ("image/png", True)
        assert preview_disposition("report.pdf") == ("application/pdf", True)

    def test_plain_text_renders_inline(self):
        media_type, inline = preview_disposition("notes.txt")
        assert media_type == "text/plain"
        assert inline is True

    def test_html_is_served_as_text_so_its_scripts_never_run(self):
        # Inline HTML would execute agent-written scripts on Kayak's own origin.
        assert preview_disposition("index.html") == ("text/plain", True)

    def test_unknown_binaries_download_instead_of_rendering(self):
        media_type, inline = preview_disposition("tool.bin")
        assert media_type == "application/octet-stream"
        assert inline is False
