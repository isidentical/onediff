import os

os.environ["ONEFLOW_MLIR_CSE"] = "1"
os.environ["ONEFLOW_MLIR_ENABLE_INFERENCE_OPTIMIZATION"] = "1"
os.environ["ONEFLOW_MLIR_ENABLE_ROUND_TRIP"] = "1"
os.environ["ONEFLOW_MLIR_FUSE_FORWARD_OPS"] = "1"
os.environ["ONEFLOW_MLIR_FUSE_OPS_WITH_BACKWARD_IMPL"] = "1"
os.environ["ONEFLOW_MLIR_GROUP_MATMUL"] = "1"
os.environ["ONEFLOW_MLIR_PREFER_NHWC"] = "1"

os.environ["ONEFLOW_KERNEL_ENABLE_FUSED_CONV_BIAS"] = "1"
os.environ["ONEFLOW_KERNEL_ENABLE_FUSED_LINEAR"] = "1"

os.environ["ONEFLOW_KERNEL_CONV_CUTLASS_IMPL_ENABLE_TUNING_WARMUP"] = "1"
os.environ["ONEFLOW_KERNEL_CONV_ENABLE_CUTLASS_IMPL"] = "1"

os.environ["ONEFLOW_CONV_ALLOW_HALF_PRECISION_ACCUMULATION"] = "1"
os.environ["ONEFLOW_MATMUL_ALLOW_HALF_PRECISION_ACCUMULATION"] = "1"

os.environ["ONEFLOW_LINEAR_EMBEDDING_SKIP_INIT"] = "1"
os.environ["ONEFLOW_RUN_GRAPH_BY_VM"] = "1"

import click
import oneflow as flow
from tqdm import tqdm


def mock_wrapper(f):
    import sys

    flow.mock_torch.enable(lazy=True)
    ret = f()
    flow.mock_torch.disable()
    # TODO: this trick of py mod purging will be removed
    tmp = sys.modules.copy()
    for x in tmp:
        if x.startswith("diffusers"):
            del sys.modules[x]
    return ret


class UNetGraph(flow.nn.Graph):
    def __init__(self, unet):
        super().__init__()
        self.unet = unet
        self.config.enable_cudnn_conv_heuristic_search_algo(False)
        self.config.allow_fuse_add_to_output(True)

    def build(self, latent_model_input, t, text_embeddings):
        text_embeddings = flow._C.amp_white_identity(text_embeddings)
        return self.unet(
            latent_model_input, t, encoder_hidden_states=text_embeddings
        ).sample


def get_graph(token):
    from diffusers import UNet2DConditionModel

    with flow.no_grad():
        unet = UNet2DConditionModel.from_pretrained(
            "runwayml/stable-diffusion-v1-5",
            use_auth_token=token,
            revision="fp16",
            torch_dtype=flow.float16,
            subfolder="unet",
        )
        unet = unet.to("cuda")
        return UNetGraph(unet)


test_seq = [2, 1, 0]


def noise_shape(batch_size, num_channels, image_w, image_h):
    sizes = (image_w // 8, image_h // 8)
    return (batch_size, num_channels) + sizes


def image_dim(i):
    return 768 + 128 * i


@click.command()
@click.option("--token")
@click.option("--repeat", default=1000)
@click.option("--sync_interval", default=50)
def benchmark(token, repeat, sync_interval):
    # create a mocked unet graph
    unet_graph = mock_wrapper(lambda: get_graph(token))

    # generate inputs with torch
    from diffusers.utils import floats_tensor
    import torch

    batch_size = 2
    num_channels = 4
    sizes = (64, 64)
    noise = (
        floats_tensor((batch_size, num_channels) + sizes).to("cuda").to(torch.float16)
    )
    print(f"{type(noise)=}")
    time_step = torch.tensor([10]).to("cuda")
    encoder_hidden_states = (
        floats_tensor((batch_size, 77, 768)).to("cuda").to(torch.float16)
    )

    # convert to oneflow tensors
    noise_of_sizes = [
        floats_tensor(noise_shape(batch_size, num_channels, image_dim(i), image_dim(j)))
        .to("cuda")
        .to(torch.float16)
        for i in test_seq
        for j in test_seq
    ]
    noise_of_sizes = [flow.utils.tensor.from_torch(x) for x in noise_of_sizes]

    [noise, time_step, encoder_hidden_states] = [
        flow.utils.tensor.from_torch(x) for x in [noise, time_step, encoder_hidden_states]
    ]
    unet_graph(noise, time_step, encoder_hidden_states)

    flow._oneflow_internal.eager.Sync()
    import time

    t0 = time.time()
    for r in tqdm(range(repeat)):
        import random
        noise = random.choice(noise_of_sizes)
        out = unet_graph(noise, time_step, encoder_hidden_states)
        # convert to torch tensors
        out = flow.utils.tensor.to_torch(out)
        if r == repeat - 1 or r % sync_interval == 0:
            flow._oneflow_internal.eager.Sync()
    print(f"{type(out)=}")
    t1 = time.time()
    duration = t1 - t0
    throughput = repeat / duration
    print(
        f"Finish {repeat} steps in {duration:.3f} seconds, average {throughput:.2f}it/s"
    )


if __name__ == "__main__":
    print(f"{flow.__path__=}")
    print(f"{flow.__version__=}")
    benchmark()
