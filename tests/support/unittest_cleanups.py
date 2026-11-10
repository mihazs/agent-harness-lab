"""Run a manually driven unittest.TestCase's cleanups and raise what they raised.

pytest fixtures that build a TestCase helper with setUp() release it outside any unittest runner. There,
TestCase.doCleanups() returns False and drops the exception, and doClassCleanups() only records it in
tearDown_exceptions, so a failed cleanup would pass silently. These helpers run the same cleanups, LIFO and
continuing after a failure as unittest does, then raise the single error or an ExceptionGroup of all of them.
"""


def _raise(errors, label):
    if len(errors) == 1:
        raise errors[0]
    if errors:
        raise BaseExceptionGroup(f'{label} failed', errors)


def run_cleanups(*cases):
    """Runs each case's addCleanup callbacks in the order the cases are given, then raises their errors."""
    errors = []
    for case in cases:
        while case._cleanups:
            function, args, kwargs = case._cleanups.pop()
            try:
                function(*args, **kwargs)
            except BaseException as error:
                errors.append(error)
    _raise(errors, 'unittest cleanups')


def run_callbacks(callbacks):
    """Run an owner's no-argument cleanup callbacks, continuing after failures."""
    errors = []
    while callbacks:
        try:
            callbacks.pop()()
        except BaseException as error:
            errors.append(error)
    _raise(errors, 'fixture cleanups')


def run_class_cleanups(cls):
    """Runs cls.doClassCleanups() and raises the errors it recorded in tearDown_exceptions."""
    cls.doClassCleanups()
    _raise([error for _, error, _ in cls.tearDown_exceptions], f'{cls.__name__} class cleanups')
