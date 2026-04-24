"""
File content search for browser-based code editor.
Provides fast full-text search with regex support.
Platform admin resource - no org scoping.

Search queries the database directly via the ``file_index`` table for code,
modules, and any other text files persisted under ``_repo/``. Form and agent
content is no longer stored as per-UUID YAML — it lives in the manifest and is
not part of the editor search surface.
"""

import re
import time
import logging
from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import SearchRequest, SearchResponse, SearchResult
from src.models.orm.file_index import FileIndex

logger = logging.getLogger(__name__)

# Maximum results per entity type to prevent overwhelming queries
MAX_RESULTS_PER_TYPE = 500


def _search_content(
    content: str,
    path: str,
    query: str,
    case_sensitive: bool,
    is_regex: bool,
) -> List[SearchResult]:
    """
    Search content string for matches.

    Args:
        content: Text content to search
        path: File path for results
        query: Search query (text or regex pattern)
        case_sensitive: Whether to match case-sensitively
        is_regex: Whether query is a regex pattern

    Returns:
        List of SearchResult objects
    """
    results: List[SearchResult] = []

    try:
        # Build regex pattern
        if is_regex:
            pattern = query
        else:
            # Escape special regex characters for literal search
            pattern = re.escape(query)

        # Compile regex with appropriate flags
        flags = 0 if case_sensitive else re.IGNORECASE
        regex = re.compile(pattern, flags)

        # Split into lines
        lines = content.split('\n')

        # Search each line
        for line_num, line in enumerate(lines, start=1):
            # Find all matches in this line
            for match in regex.finditer(line):
                # Get context lines (previous and next)
                context_before = lines[line_num - 2] if line_num > 1 else None
                context_after = lines[line_num] if line_num < len(lines) else None

                results.append(SearchResult(
                    file_path=path,
                    line=line_num,
                    column=match.start(),
                    match_text=line,
                    context_before=context_before,
                    context_after=context_after
                ))

    except (re.error, Exception) as e:
        logger.warning(f"Error searching {path}: {e}")

    return results


async def search_files_db(
    db: AsyncSession,
    request: SearchRequest,
    root_path: str = ""
) -> SearchResponse:
    """
    Search files for content matching the query using database queries.

    Searches the file_index for workflow Python code, module Python code, and
    any other indexed text content.

    Args:
        db: Database session
        request: SearchRequest with query and options
        root_path: Path prefix filter (empty = all files)

    Returns:
        SearchResponse with results and metadata

    Raises:
        ValueError: If query is invalid regex
    """
    start_time = time.time()

    # Validate regex if enabled
    if request.is_regex:
        try:
            flags = 0 if request.case_sensitive else re.IGNORECASE
            re.compile(request.query, flags)
        except re.error as e:
            raise ValueError(f"Invalid regex pattern: {str(e)}")

    all_results: List[SearchResult] = []
    files_searched = 0

    # Build file pattern filter if specified
    like_pattern = None
    if request.include_pattern:
        # Convert glob pattern to SQL LIKE pattern
        # e.g., "**/*.py" -> "%.py", "workflows/*.py" -> "workflows/%.py"
        like_pattern = request.include_pattern.replace("**/*", "%").replace("**", "%").replace("*", "%")

    # 1. Search all code files via file_index (workflows, modules, all Python)
    fi_conditions = [
        FileIndex.content.isnot(None),
    ]
    if root_path:
        fi_conditions.append(FileIndex.path.like(f"{root_path}%"))
    if like_pattern:
        fi_conditions.append(FileIndex.path.like(like_pattern))
    code_stmt = (
        select(FileIndex.path, FileIndex.content)
        .where(*fi_conditions)
        .limit(MAX_RESULTS_PER_TYPE)
    )
    code_result = await db.execute(code_stmt)
    for row in code_result:
        files_searched += 1
        if row.content:
            results = _search_content(
                row.content,
                row.path,
                request.query,
                request.case_sensitive,
                request.is_regex,
            )
            all_results.extend(results)
            if len(all_results) >= request.max_results:
                break

    # Truncate results if needed
    truncated = len(all_results) > request.max_results
    results = all_results[:request.max_results]

    # Calculate search time
    search_time_ms = int((time.time() - start_time) * 1000)

    return SearchResponse(
        query=request.query,
        total_matches=len(results),
        files_searched=files_searched,
        results=results,
        truncated=truncated,
        search_time_ms=search_time_ms
    )
