from pathlib import Path
from typing import Optional
from dataclasses import dataclass

@dataclass
class RemarkableItem:
    id: str
    visibleName: str
    trashed: bool
    parent: 'Optional[RemarkableFolder]' 

@dataclass
class RemarkableFolder(RemarkableItem):
    children: list[RemarkableItem]

@dataclass
class RemarkableBook(RemarkableItem):
    last_opened_page: int
    total_pages: int
    xochitl_files: list[Path]
    output_pdf: Path
    backing_pdf: Path
    thumbnails_dir: Path
