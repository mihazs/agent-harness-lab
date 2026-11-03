"""One opt-in alias acceptance using the existing unchanged ten-root experiment."""
from contextlib import redirect_stdout
import io
import unittest
from unittest.mock import patch

from agent_harness_lab.cli import main
if __package__:
    from . import test_conditional_application_native as native_fixture,test_sandbox_kernel as kernel
else:
    import test_conditional_application_native as native_fixture
    import test_sandbox_kernel as kernel


@unittest.skipUnless(kernel.ENABLED,'explicit delegated native scope required')
class SkillInvocationKernelTests(kernel.KernelFixture):
    # Reuse fixture helpers, never inherit its test methods or change its design.
    FINAL_METHOD=native_fixture.TenRootConditionalApplicationKernelTests.FINAL_METHOD
    FINAL_TASKS=native_fixture.TenRootConditionalApplicationKernelTests.FINAL_TASKS
    TRIAL_LIMIT=native_fixture.TenRootConditionalApplicationKernelTests.TRIAL_LIMIT
    FEASIBLE=True
    WORKER=native_fixture.TenRootConditionalApplicationKernelTests.WORKER
    sign=native_fixture.ConditionalApplicationKernelTests.sign
    policy=native_fixture.ConditionalApplicationKernelTests.policy
    qualify_bank=native_fixture.ConditionalApplicationKernelTests.qualify_bank
    reviewed_bank=native_fixture.TenRootConditionalApplicationKernelTests.reviewed_bank
    setup_authorities=native_fixture.ConditionalApplicationKernelTests.setup_authorities
    transport=native_fixture.ConditionalApplicationKernelTests.transport

    def test_installed_alias_applies_and_public_rollback_restores(self):
        destination=self.root/'fake-codex/skills'
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(['skills','--destination',str(destination),'--backup-root',str(self.root/'skill-backups')]),0)
        self.assertTrue((destination/'harness-self-improvement/references/invocation.md').is_file())
        def dispatch(argv):
            return main(['skills','invoke',*argv[1:]] if argv[:1]==['improve'] else argv)
        with patch.object(native_fixture,'main',side_effect=dispatch):
            native_fixture.ConditionalApplicationKernelTests.test_plain_improve_consumes_signed_final_then_applies_and_public_rollback_restores(self)
