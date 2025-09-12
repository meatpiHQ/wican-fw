import { glob } from "glob";
import { readFile, writeFile } from "fs/promises";
import { get_params } from "./params.js";
import { exit } from "process";

const PARAMS_TO_IGNORE = ["note", "comment", "add_to_docs"];
const PROFILE_FOLDER = import.meta.dirname + "/../../vehicle_profiles";
const PROFILE_TARGET = import.meta.dirname + "/../../vehicle_profiles.json";

export async function process_cars() {
  const files = await glob(PROFILE_FOLDER + "/**/*.json");
  let cars = [];
  let promises = [];
  const params = await get_params();
  files.forEach((file) => {
    promises.push(add_json(file, params, cars));
  });

  await Promise.all(promises);

  cars.sort((a, b) => {
    let first = a.car_model.toLowerCase();
    let second = b.car_model.toLowerCase();
    return first < second ? -1 : first > second ? 1 : 0;
  });

  await write_cars(cars);
  await save_supported_cars(cars);

  return cars;
}

async function write_cars(cars) {
  //Encode and decode to create a copy not linked to original
  cars = JSON.parse(JSON.stringify(cars));
  //Clean params to not write to file
  cars.forEach((car) => {
    Object.getOwnPropertyNames(car).forEach((field) => {
      if (PARAMS_TO_IGNORE.includes(field)) {
        delete car[field];
      }
    });
  });

  const resultString = JSON.stringify({ cars });
  //Use below line instead for generating pretty json
  // result = JSON.stringify(result, null, 2);
  await writeFile(PROFILE_TARGET, resultString);
}

async function add_json(jsonPath, params, cars) {
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

  cars.push(data);
}

async function save_supported_cars(cars) {
  const displayCars = cars.filter(
    (car) => car.add_to_docs === undefined || car.add_to_docs === true,
  );
  let supportedVehiclesListContent = await readFile(
    "src/md/supported-vehicles.md",
  );
  displayCars.forEach((car) => {
    const note = car.note ? ` (${car.note})` : "";
    supportedVehiclesListContent += `- ${car.car_model}${note}\n`;
  });

  const supportedVehiclesListFilepath = await glob(
    "../docs/content/*.Config/*.Automate/*.Supported_Vehicles.md",
  );
  if (supportedVehiclesListFilepath.length !== 1) {
    throw new Error("Unable to determine automateDirectory");
  }
  await writeFile(
    supportedVehiclesListFilepath[0],
    supportedVehiclesListContent,
  );
}
