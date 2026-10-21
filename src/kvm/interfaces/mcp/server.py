"""MCP tools over semantic platform capabilities; no hardware-specific imports."""
from mcp.server.fastmcp import FastMCP, Image
from mcp.types import TextContent
import json

from kvm.core.contracts import Backend
from kvm.core.ownership import InputGate
from kvm.automation.inspection import Inspector


def create_mcp(backend: Backend, gate: InputGate) -> FastMCP:
    vision = Inspector(backend, gate)
    mcp = FastMCP('KVM', instructions=(
        'Controls the device or desktop provided by the selected KVM platform. '
        'Check status and screenshot before acting. Coordinates are normalized to the captured display. '
        'Text input requires a US keyboard layout and suitable target input mode.'
        ' Prefer inspect_screen and click_target for visual selection. '
        'Pass snapshot_id when paging; use inspect_region for small or missing targets. '
        'After any input, inspect again; old targets expire or become invalid.'
    ))

    @mcp.tool()
    def status() -> dict:
        """Inspect device signal and input connection without sending input."""
        return {**backend.status(), 'manual_control': gate.manual}

    @mcp.tool()
    def screenshot(max_width: int = 1280) -> Image:
        """Capture a fresh JPEG, preserving aspect ratio. Width 320..3840."""
        if not 320 <= max_width <= 3840:
            raise ValueError('max_width must be 320..3840')
        return Image(data=backend.screenshot(max_width), format='jpeg')

    def content(inspection):
        instructions = (
            'Dense screen: choose a C region from the overview, then inspect_region. '
            'Use region_id="FULL" to request all detailed pages. '
            'All regions reuse the same frame; visual proposals may include decorative content.'
            if inspection.metadata['presentation'] == 'regions' else
            'Visual candidates are not guaranteed clickable. All images use the same frame. '
            'Use click_target with this snapshot_id and target ID. '
            'Use inspect_region(region_id="FULL", mode="grid") for missed targets.'
        )
        metadata = {**inspection.metadata, 'instructions': instructions}
        return [TextContent(type='text', text=json.dumps(metadata)),
                *(Image(data=data, format='png').to_image_content() for data in inspection.images)]

    @mcp.tool()
    def inspect_screen(snapshot_id: str = '', page: int = 0, page_size: int = 3) -> list:
        """Capture candidates: overview plus up to 3 magnified marked crops by default.

        Use returned snapshot_id and next_page to see more of the SAME frame.
        Dense screens return a nine-region overview first: use inspect_region.
        Candidate IDs locate visual regions, not guaranteed interactive controls.
        """
        return content(vision.inspect(snapshot_id, page, page_size))

    @mcp.tool()
    def inspect_region(snapshot_id: str, region_id: str, mode: str = 'detect',
                       page: int = 0, page_size: int = 3) -> list:
        """Refine a region/target ID, or FULL. mode: detect or grid (4x4 fallback).

        Grid cells get target IDs too; refine a cell again to locate a missing
        control precisely. All crops reuse the original snapshot, not a new frame.
        """
        return content(vision.region(snapshot_id, region_id, mode, page, page_size))

    @mcp.tool()
    def click_target(snapshot_id: str, target_id: str, button: str = 'left') -> dict:
        """Click a marked target after checking a fresh frame for visual changes.

        Reject expired/changed observations or human takeover. On success all
        observations are invalidated; inspect_screen again before another click.
        """
        return {**vision.click(snapshot_id, target_id, button), 'next': 'inspect_screen'}

    @mcp.tool()
    def press_key(keys: list[str], hold_ms: int = 50) -> str:
        """Press and release a chord, e.g. ['ctrl','alt','delete'], ['f2'], ['enter']."""
        with gate.automated(), backend.open_input() as device:
            device.press_key(keys, hold_ms)
        return 'Key chord sent and released'

    @mcp.tool()
    def type_text(text: str) -> str:
        """Type 1..1000 US-layout ASCII characters; newline presses Enter.

        Unsupported characters reject the entire request before any input is sent.
        Caps Lock and the target IME affect the resulting text.
        """
        with gate.automated(), backend.open_input() as device:
            device.type_text(text)
        return f'Sent {len(text)} characters'

    @mcp.tool()
    def mouse(x: float, y: float, button: str = 'none', wheel: int = 0) -> str:
        """Move to normalized (x,y) in [0,1], optionally click and/or scroll.

        button: none/left/right/middle. Positive wheel scrolls up. Click releases.
        Absolute coordinates assume the captured display is the target desktop.
        """
        with gate.automated(), backend.open_input() as device:
            device.click(x, y, button, wheel)
        return 'Pointer report sent and buttons released'

    @mcp.tool()
    def release_all() -> str:
        """Release keyboard and pointer buttons without overriding human ownership."""
        with gate.automated(), backend.open_input() as device:
            device.release_all()
        return 'All keys and buttons released'

    return mcp
