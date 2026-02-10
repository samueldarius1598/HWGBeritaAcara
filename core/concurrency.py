from functools import partial

from starlette.concurrency import run_in_threadpool


async def run_blocking(func, *args, **kwargs):
    return await run_in_threadpool(partial(func, *args, **kwargs))
