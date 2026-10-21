"""Read-only MCP-over-SSH smoke check; optional disconnected-device checks."""
import argparse
import asyncio
import json
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main(host, disconnected):
    params = StdioServerParameters(command='ssh', args=[
        '-T', '-o', 'BatchMode=yes', host,
        '/opt/kvm-mcp/.venv/bin/kvm-mcp', '--backend', 'rock5b-plus'
    ])
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert names == {'status', 'screenshot', 'press_key', 'type_text', 'mouse', 'release_all',
                                      'inspect_screen', 'inspect_region', 'click_target'}, names
            result = await session.call_tool('status', {})
            assert not result.isError, result
            print(result.model_dump_json(indent=2))
            state = json.loads(result.content[0].text)
            if disconnected:
                assert not state['input']['connected'], 'Disconnect target USB before negative tests'
                assert not state['video']['signal'], 'Disconnect HDMI before negative tests'
                # These checks never send input when the above state is unchanged.
                # Use screenshot only; avoid a check-then-act race with a newly connected USB host.
                image = await session.call_tool('screenshot', {})
                assert image.isError, image
                print('No-signal screenshot returns an MCP tool error as expected')
            print('MCP initialize/list_tools/status passed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('host', nargs='?', default='r5p')
    parser.add_argument('--disconnected', action='store_true')
    args = parser.parse_args()
    asyncio.run(main(args.host, args.disconnected))
