"""已验证的首帧协议配置；保持编译指纹与实际请求一致。"""

SUPPORTED_MODEL = "flux_2_klein_9b_kv_i8x.ckpt"
# 已在旧项目验证的固定配置；变更这些参数必须同步指纹版本与协议测试。
IMAGE_PROFILE = {
    "profile": "flux2-klein-v1",
    "sdk_version": "0.4.0",
    "batchCount": 1,
    "batchSize": 1,
    "strength": 1,
    "sampler": 16,
    "seedMode": 2,
    "shift": 3,
    "hiresFix": False,
    "resolutionDependentShift": False,
    "speedUpWithGuidanceEmbed": True,
    "tiledDecoding": False,
    "tiledDiffusion": False,
    "controls": [],
    "loras": [],
}
