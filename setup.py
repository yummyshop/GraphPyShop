import subprocess
from setuptools import setup, Command
from setuptools.command.build_py import build_py as _build_py
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class AriadneCodegenCommand(Command):
    description = "Run ariadne-codegen before building the package"
    user_options = []

    def initialize_options(self):
        pass

    def finalize_options(self):
        pass

    def run(self):
        subprocess.check_call(['ariadne-codegen'])

class CustomBuildCommand(_build_py):
    def run(self):
        self.run_command('ariadne_codegen')
        super().run()

setup(
    cmdclass={
        'ariadne_codegen': AriadneCodegenCommand,
        'build_py': CustomBuildCommand,
    },
)