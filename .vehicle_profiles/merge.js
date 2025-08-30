import { glob } from "glob";
import { readFile, writeFile } from "fs/promises";
import editJsonFile from "edit-json-file";
import path from "path";

const source_folder = "../vehicle_profiles";
const target = "../vehicle_profiles.json";

const files = await glob(source_folder + "/**/*.json");
const params = JSON.parse(await readFile("params.json"));

let param_array = Object.getOwnPropertyNames(params);
let schema_file = editJsonFile("schema.json");
const PARAM_PATH =
  "properties.pids.items.properties.parameters.propertyNames.enum";
let existing_params = schema_file.get(PARAM_PATH);
if (existing_params !== param_array) {
  schema_file.set(PARAM_PATH, param_array);
  schema_file.save();
}

let result = {
  cars: [],
};

async function add_json(jsonPath) {
  let data = JSON.parse(await readFile(jsonPath));
  if (!data.pids || typeof data.pids !== "object") {
    console.warn(`Warning: Skipping file without valid 'pids': ${jsonPath}`);
    return;
  }
  Object.keys(data.pids).forEach((key) => {
    let newParams = [];
    for (const [param, exp] of Object.entries(data.pids[key].parameters)) {
      newParams.push({
        name: param,
        expression: exp,
        ...params[param].settings,
      });
    }
    data.pids[key].parameters = newParams;
  });

  // Attempt to load an optional note from supported.md in the same folder.
  // Rules:
  //  - Look only at supported.md.
  //  - If a line matches '<exact car_model>: some note', use that note (case-insensitive match on car_model).
  //  - Else use the first non-empty, non-heading line as a generic note for all models in that folder.
  try {
    const dir = path.dirname(jsonPath);
    try {
      const raw = await readFile(path.join(dir, "supported.md"), {
        encoding: "utf8",
      });
      const lines = raw
        .split(/\r?\n/)
        .map((l) => l.trim())
        .filter((l) => l.length > 0 && !l.startsWith("#"));
      if (lines.length > 0) {
        const carModel = data.car_model || "";
        const normalizedVariants = (() => {
          const parts = carModel
            .split(":")
            .map((p) => p.trim())
            .filter(Boolean);
          const noColon = carModel
            .replace(/:/g, "")
            .replace(/\s+/g, " ")
            .trim();
          const joinedSpace = parts.join(" ");
          const modelOnly =
            parts.length > 1 ? parts.slice(1).join(" ") : carModel;
          return [
            carModel,
            carModel.toLowerCase(),
            noColon,
            noColon.toLowerCase(),
            joinedSpace,
            joinedSpace.toLowerCase(),
            modelOnly,
            modelOnly.toLowerCase(),
          ].filter((v, i, a) => v && a.indexOf(v) === i);
        })();
        let noteText = null;
        for (const line of lines) {
          const idx = line.indexOf(":");
          if (idx === -1) {
            continue;
          }
          const prefix = line.substring(0, idx).trim();
          const prefixNorms = [
            prefix,
            prefix.toLowerCase(),
            prefix.replace(/:/g, "").replace(/\s+/g, " ").trim(),
            prefix.replace(/:/g, "").replace(/\s+/g, " ").trim().toLowerCase(),
          ];
          const matches = prefixNorms.some((p) =>
            normalizedVariants.includes(p),
          );
          if (matches) {
            noteText = line.substring(idx + 1).trim();
            break;
          }
        }
        // Fallback: if no direct match, but first line contains a colon, use text after colon; else entire first line.
        if (!noteText) {
          const first = lines[0];
          const idx = first.indexOf(":");
          noteText = idx !== -1 ? first.substring(idx + 1).trim() : first;
        }
        if (noteText) {
          const cleaned = noteText.replace(/^#+\s*/, "");
          data.note = cleaned.startsWith("(") ? cleaned : `(${cleaned})`;
        }
      }
    } catch (_) {
      // README absent; ignore
    }
  } catch (e) {
    console.warn("Note detection failed for", jsonPath, e.message);
  }

  result.cars.push(data);
}

let promises = [];
files.forEach((file) => {
  promises.push(add_json(file));
});

await Promise.all(promises);

result.cars.sort((a, b) => {
  let first = a.car_model.toLowerCase();
  let second = b.car_model.toLowerCase();
  return first < second ? -1 : first > second ? 1 : 0;
});

const resultString = JSON.stringify(result);
//Use below line instead for generating pretty json
// result = JSON.stringify(result, null, 2);
await writeFile(target, resultString);

const displayCars = result.cars.filter(
  (car) => car.car_model !== "AAA: Generic",
);
let supportedVehiclesListContent = `<!--

================================================================
THIS FILE WAS GENERATED! DO NOT UPDATE OR YOUR CHANGES ARE LOST!
================================================================

-->
# ⚠️ Important Note

**Disclaimer:**  
The list below is primarily based on **user submissions**. Vehicle profiles are **tested and verified** through user feedback.  
Before ordering a **WiCAN adapter**, please review the following important notes:

- For **detailed information on compatibility with specific makes and models**, you can search the relevant **GitHub issues**. Users often report experiences and others share helpful tips—it's a great resource for troubleshooting and identifying vehicle-specific behavior.

---

### 1. **WiCAN PRO Recommended**
We **highly recommend** ordering the **WiCAN PRO** version because:
- It features an **advanced OBD chip**, improving compatibility with a wider range of vehicles.
- You’ll receive **access to the latest features** and **firmware updates**.
- It offers **better long-term support** compared to the standard WiCAN **because of memory limitations on the original WiCAN hardware**.

---

### 2. **Vehicle Alarm Triggers**
On some vehicles, leaving an **OBD adapter** connected and **polling the ECU** while the car is **off** may **trigger the vehicle’s alarm**.

- This behavior is **controlled by the vehicle’s firmware**.
- In some cases, this feature **can be disabled** in the car’s settings.
- In other cases, there may be **no option to turn it off**.

---

# 🚗 Vehicle Support Overview

WiCAN **vehicle profiles** are available for various models listed below. These profiles include **vehicle-specific parameters**—especially for **electric vehicles**—but also occasionally for **standard (non-electric)** models (e.g., **odometer readings**, **fuel levels**).

In most **electric, hybrid, and standard vehicles**, a set of **standard OBD-II PIDs** is supported—but the availability of these PIDs depends on the **make and model**:

- Some manufacturers **disable certain standard PIDs**.
- Others **override or replace** them with **proprietary, manufacturer-specific PIDs**.
- For this reason, even vehicles from the same brand can offer **different data access**.

You can try scanning available **standard PIDs** using the **Automate** tab:

🔗 [Scan Standard PIDs →](https://meatpihq.github.io/wican-fw/config/automate/usage#standard-pids)

---

### 💡 Tip for Vehicle-Specific Info
When you're curious about whether your vehicle is supported or how it behaves with WiCAN:
- **Search GitHub issues** for your vehicle’s make and model.
- Many users share firsthand experiences, common pitfalls, and working configurations.

# Supported Vehicles
`;
displayCars.forEach((car) => {
  const note = car.note ? ` ${car.note}` : "";
  supportedVehiclesListContent += `- ${car.car_model}${note}\n`;
});

const supportedVehiclesListFilepath = await glob(
  "../docs/content/*.Config/*.Automate/*.Supported_Vehicles.md",
);
if (
  supportedVehiclesListFilepath.length == 0 ||
  supportedVehiclesListFilepath.length != 1
) {
  throw new Error("Unable to determine automateDirectory");
}
await writeFile(supportedVehiclesListFilepath[0], supportedVehiclesListContent);

// Generate Supported_Parameters.md
const supportedParametersListFilepath = await glob(
  "../docs/content/*.Config/*.Automate/*.Supported_Parameters.md",
);
let supportedParametersTargetPath = null;
if (supportedParametersListFilepath.length === 1) {
  supportedParametersTargetPath = supportedParametersListFilepath[0];
} else if (supportedParametersListFilepath.length === 0) {
  // Default to the same directory as Supported_Vehicles.md, with the expected filename
  const vehiclesPath = supportedVehiclesListFilepath[0];
  const dir = vehiclesPath.substring(0, vehiclesPath.lastIndexOf("/"));
  supportedParametersTargetPath = `${dir}/4.Supported_Parameters.md`;
} else {
  throw new Error(
    "Unable to determine automateDirectory for Supported_Parameters.md",
  );
}

let supportedParametersContent = `<!--

================================================================
THIS FILE WAS GENERATED! DO NOT UPDATE OR YOUR CHANGES ARE LOST!
================================================================

-->
# Supported Parameters

This table lists all supported parameters, their descriptions, and settings as used in WiCAN vehicle profiles.

| Parameter | Description | Settings |
|-----------|-------------|----------|
`;

param_array.forEach((param) => {
  const entry = params[param];
  // Some entries may have typo 'decription' instead of 'description'
  const desc = entry.description || entry.decription || "";
  // Format settings as JSON, but compact
  const settings = entry.settings
    ? Object.entries(entry.settings)
        .map(([k, v]) => `${k}: ${v}`)
        .join(", ")
    : "";
  supportedParametersContent += `| \`${param}\` | ${desc} | ${settings} |\n`;
});

await writeFile(supportedParametersTargetPath, supportedParametersContent);
