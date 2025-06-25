import { glob } from "glob";
import { readFile, writeFile } from "fs/promises";
import editJsonFile from "edit-json-file";

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

async function add_json(path) {
  let data = JSON.parse(await readFile(path));
  if (!data.pids || typeof data.pids !== "object") {
    console.warn(`Warning: Skipping file without valid 'pids': ${path}`);
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

const models = result.cars
  .map((car) => car.car_model)
  .filter((model) => model !== "AAA: Generic");
let supportedVehiclesListContent = `<!--

================================================================
THIS FILE WAS GENERATED! DO NOT UPDATE OR YOUR CHANGES ARE LOST!
================================================================

-->
# Supported Vehicles
For vehicles listed below a WiCAN vehicle profiles exists:
`;
models.forEach((model) => (supportedVehiclesListContent += `- ${model}\n`));

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
if (
  supportedParametersListFilepath.length == 0 ||
  supportedParametersListFilepath.length != 1
)
  throw new Error("Unable to determine automateDirectory for Supported_Parameters.md");

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
  const settings = entry.settings ?
    Object.entries(entry.settings)
      .map(([k, v]) => `${k}: ${v}`)
      .join(", ") :
    "";
  supportedParametersContent += `| \`${param}\` | ${desc} | ${settings} |\n`;
});

await writeFile(supportedParametersListFilepath[0], supportedParametersContent);
