"""Generate editable Python calls from validated semantic recording steps."""

def generate_script(data):
    lines = ['"""Generated visual replay. Set KVM_BACKEND before running."""',
             'from pathlib import Path','from wirehand import connect','from wirehand.automation import Recording','',
             'flow = Recording.load(Path(__file__).parent)','',
             'with connect() as computer:']
    for step in data['steps']:
        if step.get('delay',0):lines.append(f'    computer.sleep({step["delay"]!r})')
        args = dict(step['args'])
        target = args.pop('target',None)
        params = []
        if step['action']=='hotkey':
            params.extend(repr(k) for k in args.pop('keys'))
        if target:params.append(f'target=flow.target({target!r})')
        params.extend(f'{key}={value!r}' for key,value in args.items())
        lines.append(f'    computer.{step["action"]}({", ".join(params)})')
    if not data['steps']:lines.append('    pass')
    return '\n'.join(lines)+'\n'
