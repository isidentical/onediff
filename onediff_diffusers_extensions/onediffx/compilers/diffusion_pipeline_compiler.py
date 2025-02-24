import os
from onediff.infer_compiler import oneflow_compile
from onediff.infer_compiler.with_oneflow_compile import DeployableModule
from onediff.infer_compiler.utils.log_utils import logger


def _recursive_getattr(obj, attr, default=None):
    attrs = attr.split(".")
    for attr in attrs:
        if not hasattr(obj, attr):
            return default
        obj = getattr(obj, attr, default)
    return obj


def _recursive_setattr(obj, attr, value):
    attrs = attr.split(".")
    for attr in attrs[:-1]:
        obj = getattr(obj, attr)
    setattr(obj, attrs[-1], value)


_PARTS = [
    "text_encoder",
    "text_encoder_2",
    "image_encoder",
    "unet",
    "controlnet",
    "fast_unet",  # for deepcache
    "vae.decoder",
    "vae.encoder",
]

def _filter_parts(ignores=()):
    filtered_parts = []
    for part in _PARTS:
        skip = False
        for ignore in ignores:
            if part == ignore or part.startswith(ignore + "."):
                skip = True
                break
        if not skip:
            filtered_parts.append(part)

    return filtered_parts

def compile_pipe(
    pipe, *, ignores=(),
):
    filtered_parts = _filter_parts(ignores=ignores)
    for part in filtered_parts:
        obj = _recursive_getattr(pipe, part, None)
        if obj is not None:
            logger.info(f"Compiling {part}")
            _recursive_setattr(pipe, part, oneflow_compile(obj))

    if "image_processor" not in ignores:
        logger.info("Patching image_processor")

        from onediffx.utils.patch_image_processor import (
            patch_image_prcessor as patch_image_prcessor_,
        )

        patch_image_prcessor_(pipe.image_processor)

    return pipe


def save_pipe(
    pipe, dir="cached_pipe", *, ignores=(), overwrite=True
):
    if not os.path.exists(dir):
        os.makedirs(dir)
    filtered_parts = _filter_parts(ignores=ignores)
    for part in filtered_parts:
        obj = _recursive_getattr(pipe, part, None)
        if (
            obj is not None
            and isinstance(obj, DeployableModule)
            and obj._deployable_module_dpl_graph is not None
            and obj.get_graph().is_compiled
        ):
            if not overwrite and os.path.isfile(os.path.join(dir, part)):
                logger.info(f"Compiled graph already exists for {part}, not overwriting it.")
                continue
            logger.info(f"Saving {part}")
            obj.save_graph(os.path.join(dir, part))


def load_pipe(
    pipe, dir="cached_pipe", *, ignores=(),
):
    if not os.path.exists(dir):
        return
    filtered_parts = _filter_parts(ignores=ignores)
    for part in filtered_parts:
        obj = _recursive_getattr(pipe, part, None)
        if obj is not None and os.path.exists(os.path.join(dir, part)):
            logger.info(f"Loading {part}")
            obj.load_graph(os.path.join(dir, part))

    if "image_processor" not in ignores:
        logger.info("Patching image_processor")

        from onediffx.utils.patch_image_processor import (
            patch_image_prcessor as patch_image_prcessor_,
        )

        patch_image_prcessor_(pipe.image_processor)
