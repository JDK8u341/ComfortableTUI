// Comfortable shader package v1: premultiplied BGRA8 input, RGBA shader values.
cbuffer P : register(b0) { float4 crop; float4 params; float4 tint; float4 info; };
Texture2D image : register(t0);
SamplerState sampleLinear : register(s0);
float4 main(float2 uv : TEXCOORD0) : SV_Target {
    float4 c = image.SampleLevel(sampleLinear, uv, 0);
    float luma = dot(c.rgb, float3(.2126, .7152, .0722));
    float3 mapped = lerp(float3(.12,.09,.24)*c.a, float3(.35,.95,.78)*c.a, c.a > .001 ? luma/c.a : 0);
    return float4(lerp(c.rgb, mapped, saturate(params.x)), c.a);
}
