"""Recording action schema and validation, without device access."""
import math

from kvm.core.keys import MODIFIERS

ACTIONS = {'click','scroll','move_path','drag','key_sequence','hotkey','write'}
PARAMETERS = {
    'click':({'target'},{'target','button','clicks','interval','modifiers','hold_ms'}),
    'scroll':({'target','clicks'},{'target','clicks','modifiers'}),
    'move_path':({'target','path'},{'target','path','modifiers'}),
    'drag':({'target','path'},{'target','path','button','modifiers'}),
    'key_sequence':({'target','events'},{'target','events'}),
    'hotkey':({'target','keys'},{'target','keys','hold_ms'}),
    'write':({'target','text'},{'target','text'}),
}


def validate_step(step):
    action,args=step.get('action'),step.get('args')
    if action not in ACTIONS or not isinstance(args,dict):
        raise ValueError('Unsupported recording action')
    required,allowed=PARAMETERS[action]
    if not required<=args.keys() or args.keys()-allowed:
        raise ValueError('Invalid recording action parameters')
    if not isinstance(args['target'],str):raise ValueError('Invalid target reference')
    modifiers=args.get('modifiers',[])
    if not isinstance(modifiers,list) or any(k not in MODIFIERS for k in modifiers):
        raise ValueError('Invalid modifiers')
    if args.get('button','left') not in ('left','right','middle'):
        raise ValueError('Invalid mouse button')
    if 'clicks' in args:
        limit=(1,3) if action=='click' else (-2000,2000)
        if type(args['clicks']) is not int or not limit[0]<=args['clicks']<=limit[1]:
            raise ValueError('Invalid click/scroll count')
    if 'interval' in args and (not isinstance(args['interval'],(int,float)) or not 0<=args['interval']<=1):
        raise ValueError('Invalid click interval')
    if action=='click' and (type(args.get('hold_ms',30)) is not int or not 1<=args.get('hold_ms',30)<=30000):
        raise ValueError('Invalid mouse hold time')
    if action=='write' and (not isinstance(args['text'],str) or not 1<=len(args['text'])<=1000):
        raise ValueError('Invalid recorded text')
    if action=='hotkey':
        if not isinstance(args['keys'],list) or not args['keys'] or not all(isinstance(k,str) for k in args['keys']):
            raise ValueError('Invalid hotkey')
        if type(args.get('hold_ms',50)) is not int or not 10<=args.get('hold_ms',50)<=1000:
            raise ValueError('Invalid hold time')
    for field in ('events','path'):
        if field not in args:continue
        entries=args[field]
        if not isinstance(entries,list) or not 1<=len(entries)<=(10000 if field=='events' else 2000):
            raise ValueError('Invalid event sequence')
        for entry in entries:
            if not isinstance(entry,dict) or set(entry)!=({'keys','delay'} if field=='events' else {'dx','dy','delay'}):
                raise ValueError('Invalid event fields')
            if not isinstance(entry['delay'],(int,float)) or not 0<=entry['delay']<=30:
                raise ValueError('Invalid event delay')
            if field=='events':
                if not isinstance(entry['keys'],list) or not all(isinstance(k,str) for k in entry['keys']):
                    raise ValueError('Invalid key state')
            elif not all(isinstance(entry[k],(int,float)) and math.isfinite(entry[k]) for k in ('dx','dy')):
                raise ValueError('Invalid drag coordinates')
        if field=='events' and entries[-1]['keys']:
            raise ValueError('Key sequence leaves keys held')
