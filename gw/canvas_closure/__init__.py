# -*- coding: utf-8 -*-
# Copyright 2026 Gods-Workbench Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""画布闭环领域包（Phase 10E）。

对外只暴露最小契约面：稳定 ID 口径、CAS 乐观锁、零伪造与 fail-closed。
"""

from gw.canvas_closure.models import (  # noqa: F401
    CANVAS_ASSET_ATTACH_NOT_INTEGRATED,
    CANVAS_ASSET_DOWNLOAD_NOT_INTEGRATED,
    SHARED_FOLDER_IMPORT_NOT_INTEGRATED,
    SHARED_FOLDER_NOT_FOUND,
    VIDEO_RENDERER_NOT_INTEGRATED,
    VIDEO_TASK_NOT_FOUND,
)
from gw.canvas_closure.service import (  # noqa: F401
    CanvasClosureService,
    SharedFolderService,
    default_canvas_closure_service,
    default_shared_folder_service,
)

__all__ = [
    "CanvasClosureService",
    "SharedFolderService",
    "default_canvas_closure_service",
    "default_shared_folder_service",
]
