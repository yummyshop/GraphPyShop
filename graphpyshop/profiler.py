import os
import time
from datetime import datetime

profiles_dir = os.path.join(os.path.dirname(__file__), "..", "profiles")
profiles_dir = os.path.abspath(profiles_dir)


class CallGrindProfiler:
    def __init__(self, description="", enabled=True):
        self.description = description
        self.profiler = None
        self.start = None
        self.enabled = enabled

    def __enter__(self):
        if not self.enabled:
            return
        import cProfile

        self.profiler = cProfile.Profile()
        self.profiler.enable()
        self.start = time.perf_counter()

    def __exit__(self, *args):
        if not self.enabled:
            return
        self.profiler.disable()

        duration = (time.perf_counter() - self.start) * 1000
        print(f"{self.description} took {duration:.1f} ms during profiling")
        self.save_callgrind()

    def save_callgrind(self):
        from pyprof2calltree import convert, visualize

        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")  # noqa
        callgrind_filename = (
            f"{profiles_dir}/" f"callgrind.{self.description}_{timestamp}.callgrind"
        )
        os.makedirs(profiles_dir, exist_ok=True)
        profiler_stats = self.profiler.getstats()
        convert(profiler_stats, callgrind_filename)
        visualize(profiler_stats)
