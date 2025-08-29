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
          const parts = carModel.split(":").map((p) => p.trim()).filter(Boolean);
          const noColon = carModel.replace(/:/g, "").replace(/\s+/g, " ").trim();
          const joinedSpace = parts.join(" ");
          const modelOnly = parts.length > 1 ? parts.slice(1).join(" ") : carModel;
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
          const matches = prefixNorms.some((p) => normalizedVariants.includes(p));
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
# Supported Vehicles
WiCAN vehicle profiles are available for the vehicles listed below. These profiles contain vehicle-specific parameters, primarily for electric vehicles, but in some cases, standard vehicles may also have specific parameters such as odometer readings or fuel level.

If your vehicle is a non-electric or hybrid model, you can try scanning standard PIDs using the Automate tab:
https://meatpihq.github.io/wican-fw/config/automate/usage#standard-pids
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
