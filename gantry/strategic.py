"""The strategic page's model (#23): the sites that were built.

built_sites is the one definition of "built" every strategic figure uses
(summary status `built`, in config order). It reads a BuildResult's
summaries only and imports nothing from gantry.build.
"""


def built_sites(result) -> tuple[str, ...]:
    """The names of the sites with summary status `built`, in config order."""
    return tuple(s.name for s in result.summaries if s.status == "built")
