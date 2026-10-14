# RunningHub workflow 2108837566177959938 fixtures

Provenance notes for the two fixtures covering RunningHub workflow
`2108837566177959938` (“角色资产4格详图制作【Qwen-Image2.1】”, a Qwen-Image 2.1
4-panel character sheet workflow).

## `workflow_2108837566177959938_public_detail.json` — real capture

The unmodified public API response, captured **2026-10-14** from:

```
POST https://www.runninghub.cn/api/portal/workflow/detail
```

for workflow id `2108837566177959938`. The public (non-owner) response returns
`data.workflowContent: null` — RunningHub only exposes metadata to anonymous
callers: the workflow name, `nodeCount`, and the lists `primitiveNodes`,
`customNodes`, and `usedModels`. The actual prompt JSON is **not** available
publicly.

## `workflow_2108837566177959938_api_prompt_synthetic.json` — synthetic, hand-modeled

Because `workflowContent` is null on the public endpoint, the ComfyUI
API-format prompt map was **hand-modeled from the workflow's canvas screenshot
on RunningHub**, using the node classes from the real `public_detail.json`
response to anchor which nodes exist. It is a plausible reconstruction, not a
byte-for-byte capture: input values that are not readable from the screenshot
were chosen to be consistent with the visible links and the models listed in
`usedModels`.

It must remain a **pure prompt map** — top-level keys are node ids (as
strings), each mapping to `{class_type, inputs, _meta}`. Do not add wrapper
keys (e.g. a provenance/metadata envelope); provenance lives in this README.

Details worth keeping in sync with the file:

- Node `"13"` `ResolutionSelector` outputs `width` at slot **0** and `height`
  at slot **1** (consumed by `"479"` `EmptyLatentImage`).
- Node `"484"` `TextEncodeQwenImage21` outputs `positive` at slot **0**,
  `negative` at slot **1**, and `latent` at slot **2** (consumed by `"3"`
  `KSampler` and the `"482"` `ComfySwitchNode` switch path).
- The input key order within `"484"` and `"3"` matches the modeled canvas
  ordering; contract tests may rely on it.
