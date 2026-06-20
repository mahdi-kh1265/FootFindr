# AI Datasheet Profile Schema

## Purpose

FootFindr's AI sidecar extracts structured part profiles from datasheet
PDFs/text.  This is used to accelerate IC profile creation -- turning a
vendor datasheet into a draft `DraftICProfile` that an engineer can review
and approve.

## Critical Safety Rules

1. **AI output NEVER directly modifies KiCad files.**
   The AI sidecar creates draft YAML profiles that must be reviewed.

2. **All AI-generated profiles are marked `human_approved: false`.**
   This flag must be explicitly set to `true` by a human before the
   profile can be used for automated resolution.

3. **Draft profiles are informational, not authoritative.**
   Engineers must verify pin assignments, voltage ratings, thermal data,
   and recommended support parts before approving.

## DraftICProfile Schema

```yaml
mpn: string                    # Manufacturer Part Number
aliases: [string]              # Alternative part numbers / suffixes
package: string                # Package type (e.g. "QFN-20", "SOIC-8")
pins:
  - number: string             # Pin number
    name: string               # Pin name (e.g. "VIN", "GND")
    function: string            # Pin function category
    # function values: power_input, power_output, ground, signal_input,
    #   signal_output, analog_input, analog_output, digital_io,
    #   no_connect, thermal_pad
recommended_support_parts:
  - pin: string                # Pin name this part supports
    role: string               # Role (input_decoupling, output_cap, etc.)
    component_type: string     # capacitor, resistor, inductor, etc.
    value: string              # "10uF", "100nF", "10k"
    voltage_min: string        # Optional minimum voltage rating
    notes: string              # Placement / application notes
human_approved: false          # MUST be false for AI output
confidence: float              # AI confidence score (0.0 - 1.0)
source_documents: [string]     # List of source document paths/names
notes: string                  # Additional context or warnings
```

## Expected JSON Output Shape

When the AI provider processes a datasheet, it should return:

```json
{
  "mpn": "LMH6702MA/NOPB",
  "aliases": ["LMH6702MA"],
  "package": "SOIC-8",
  "pins": [
    {"number": "1", "name": "NC", "function": "no_connect"},
    {"number": "2", "name": "IN-", "function": "analog_input"},
    {"number": "3", "name": "IN+", "function": "analog_input"},
    {"number": "4", "name": "V-", "function": "power_input"},
    {"number": "5", "name": "OUT", "function": "analog_output"},
    {"number": "6", "name": "V+", "function": "power_input"},
    {"number": "7", "name": "NC", "function": "no_connect"},
    {"number": "8", "name": "NC", "function": "no_connect"}
  ],
  "recommended_support_parts": [
    {
      "pin": "V+",
      "role": "power_decoupling",
      "component_type": "capacitor",
      "value": "100nF",
      "notes": "Place within 5mm of V+ pin"
    },
    {
      "pin": "V-",
      "role": "power_decoupling",
      "component_type": "capacitor",
      "value": "100nF",
      "notes": "Place within 5mm of V- pin"
    }
  ],
  "human_approved": false,
  "confidence": 0.75,
  "source_documents": ["LMH6702_datasheet.pdf"],
  "notes": "Auto-extracted - verify pin assignments and support caps"
}
```

## Example Prompt for IC Profile Extraction

```
You are a hardware engineer's assistant. Extract structured data from
the following datasheet text for the IC with MPN "{mpn}".

Return a JSON object with:
- mpn: the part number
- aliases: any alternative part numbers
- package: the package type
- pins: array of {number, name, function} for each pin
- recommended_support_parts: decoupling caps, bias resistors, etc.
- confidence: your confidence in the extraction (0.0-1.0)
- notes: any warnings or uncertainties

Pin function values must be one of: power_input, power_output, ground,
signal_input, signal_output, analog_input, analog_output, digital_io,
no_connect, thermal_pad.

Datasheet text:
---
{datasheet_text}
---
```

## How a Future AI Provider Plugs In

1. Implement the `AIProvider` abstract class in `src/footfindr/ai/provider.py`
2. Override `extract_ic_profile(datasheet_text, mpn) -> DraftICProfile`
3. Override `is_available() -> bool`
4. Register in a config file or CLI flag (e.g. `--ai-provider openai`)
5. The provider calls the LLM API, parses the response, and returns a
   `DraftICProfile` object
6. The profile drafter enforces `human_approved = false` regardless of
   what the AI returns

Example future provider:

```python
class OpenAIProvider(AIProvider):
    def __init__(self, api_key: str, model: str = "gpt-4"):
        self._client = openai.OpenAI(api_key=api_key)
        self._model = model

    def extract_ic_profile(self, datasheet_text, mpn):
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": PROMPT.format(...)}],
            response_format={"type": "json_object"},
        )
        data = json.loads(response.choices[0].message.content)
        return DraftICProfile(**data, human_approved=False)

    def is_available(self):
        return bool(self._client)
```
