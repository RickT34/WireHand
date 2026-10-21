"""Computer automation, visual targets, recording and replay without MCP."""
from .computer import Computer, AutomationInterrupted
from .recording import Recorder, Recording
from .targets import VisualTarget, Match, TargetNotFound, AmbiguousTarget

__all__ = ['Computer','Recorder','Recording','VisualTarget','Match',
           'AutomationInterrupted','TargetNotFound','AmbiguousTarget']
