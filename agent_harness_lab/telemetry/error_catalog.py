"""Content-free tool-error classes from a fixed catalog of runtime-generated messages.

A tool error result is compared with patterns of the messages the Claude Code runtime itself writes. Only a
match yields a class; every other error body is `unclassified`, and no text, excerpt or hash of the body is kept.
A class says the result begins with a catalog message, not which component wrote it: a tool that returns the same
words as its own error is indistinguishable. The catalog applies only to the runtime releases listed in RUNTIMES,
whose own message strings were compared with these patterns; any other release is `unclassified`.
"""
import re

VERSION = 'ahl-tool-error-catalog-v1'
# Widen by comparing the release's own message strings with ENTRIES; the adapter must also verify the release.
RUNTIMES = ('2.1.322',)
UNCLASSIFIED = 'unclassified'
HEAD = 4096  # only the leading characters are compared, so an oversized body costs no more than a short one
WRAPPER = ('<tool_use_error>', '</tool_use_error>')
# Ordered, first match wins: a timed-out command must not fall through to the plain non-zero exit below it.
ENTRIES = tuple((name, re.compile(pattern)) for name, pattern in (
    ('read_before_edit', r'File has not been read yet\. Read it first before writing to it\.\Z'),
    ('file_changed_since_read', r'File has been modified since read, either by the user or by a linter\. '
        r'Read it again before attempting to write it\.\Z'),
    ('edit_target_not_unique', r'Found [1-9][0-9]{0,8} matches of the string to replace, but replace_all is false\. '
        r'To replace all occurrences, set replace_all to true\. To replace only one occurrence, '
        r'please provide more context to uniquely identify the instance\.(?:\nString: |\Z)'),
    ('edit_target_not_found', r'String to replace not found in file\.(?:\nString: |\Z)'),
    ('file_not_found', r'File does not exist\.(?: Note: your current working directory is |\Z)'),
    ('permission_denied', r"Claude requested permissions to use [^\n]{1,300}, but you haven't granted it yet\.\Z|"
        r'Permission to (?:use|read) [^\n]{1,500} has been denied\.(?:\s|\Z)'),
    ('user_rejected', r"The user doesn't want to proceed with this tool use\. The tool use was rejected "
        r'\(eg\. if it was a file edit, the new_string was NOT written to the file\)\. '
        r'(?:STOP what you are doing and wait for the user to tell you how to proceed\.|To tell you how to proceed, the user said:)'),
    ('user_interrupted', r'\[Request interrupted by user for tool use\]\Z'),
    ('command_timeout', r'Exit code [1-9][0-9]{0,2}\n(?:\[Request interrupted by user for tool use\]\n)?Command timed out after [0-9]'),
    ('command_exit_nonzero', r'Exit code [1-9][0-9]{0,2}(?:\n|\Z)'),
    ('unknown_tool', r'Error: No such tool available: [A-Za-z0-9_.:-]{1,200}(?:\.|\s|\Z)'),
    ('input_validation', r'InputValidationError: '),
))
CLASSES = tuple(name for name, _ in ENTRIES)
VOCABULARY = frozenset(CLASSES) | {UNCLASSIFIED}


def _text(content):
    """The text a result carries as a string or as text blocks; anything else has none."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(block['text'] for block in content
                         if isinstance(block, dict) and block.get('type') == 'text' and isinstance(block.get('text'), str))
    return ''


def classify(content, runtime_version):
    """The class of one tool error result's content for a runtime release, or `unclassified`; never returns text."""
    if runtime_version not in RUNTIMES:
        return UNCLASSIFIED
    text = _text(content).strip()
    if text.startswith(WRAPPER[0]) and text.endswith(WRAPPER[1]):
        text = text[len(WRAPPER[0]):-len(WRAPPER[1])]
    # A NUL after a cut head keeps a message that continues past HEAD from reading as complete.
    head = text[:HEAD] + '\0' * (len(text) > HEAD)
    return next((name for name, pattern in ENTRIES if pattern.match(head)), UNCLASSIFIED)
