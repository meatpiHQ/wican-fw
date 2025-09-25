import { glob } from "glob";
import { readFile, writeFile } from "fs/promises";
import { get_params } from "./params.js";

const PARAMS_TO_IGNORE = ["note", "comment", "add_to_docs", "extends"];
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

  const resultString = JSON.stringify({ cars }, null, 2);
  //Use below line instead for generating pretty json
  // result = JSON.stringify(result, null, 2);
  await writeFile(PROFILE_TARGET, resultString);
}

async function add_json(jsonPath, params, allCars) {
  let cars = [];
  cars.push(await read_profile(jsonPath));
  let next = cars[0].extends;
  while (next !== undefined) {
    let car = await read_profile(PROFILE_FOLDER + "/" + next);
    cars.push(car);
    next = car.extends;
  }

  if (cars.length === 1) {
    allCars.push(await process_profile(cars[0], params));
    return;
  }

  //Start with original car, but no PIDS
  //Copy without refrence, so we don't destroy the real first car
  let car = JSON.parse(JSON.stringify(cars[0]));
  car.pids = [];
  while (cars.length > 0) {
    let newCar = cars.pop();
    //pids are now optional, extends could be used just to add a 2nd name
    if (newCar.pids === undefined) {
      newCar.pids = [];
    }
    let newpids = [];
    //Get new PID's, these are what we want to add/update to profile
    newCar.pids.forEach((pid) => {
      newpids = newpids.concat(Object.keys(pid.parameters));
    });

    //Delete anything we have new versions of
    car.pids.forEach((pid) => {
      Object.keys(pid.parameters).forEach((param) => {
        if (newpids.includes(param)) {
          delete pid.parameters[param];
        }
      });
    });

    //merge them together
    let pidtokey = {};
    car.pids.forEach((pid, key) => {
      pidtokey[pid.pid] = key;
    });
    newCar.pids.forEach((pid) => {
      if (Object.keys(pidtokey).includes(pid.pid)) {
        pid.parameters = { ...pid.parameters, ...car.parameters };
      } else {
        car.pids.push(pid);
      }
    });
  }

  car.pids.forEach((pid_key) => {
    Object.keys(car.pids[pid_key].parameters).forEach((param) => {
      if(car.pids[pid_key].parameters[param] == ""){
        delete car.pids[pid_key].parameters[param]
      }
    });
  });

  //Cleanup pids with no params
  car.pids = car.pids.filter((pid) => Object.keys(pid.parameters).length > 0);

  allCars.push(await process_profile(car, params));
}

async function read_profile(path) {
  return JSON.parse(await readFile(path));
}

async function process_profile(data, params) {
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

  return data;
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
