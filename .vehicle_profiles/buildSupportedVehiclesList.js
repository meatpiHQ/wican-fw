import { readFile, writeFile } from 'fs/promises';
import { glob } from 'glob'

const vehicleProfilesJson = '../vehicle_profiles.json'
const automateDirectory = await glob('../docs/content/*.Config/*.Automate')
const target = `${automateDirectory}/2.Supported_Vehicles.md`

const vehicleProfiles = JSON.parse(await readFile(vehicleProfilesJson));
const models = vehicleProfiles.cars.map(car => car.car_model).filter(model => model !== 'AAA: Generic');

let result = '# Supported Vehicles\n';
result += 'For vehicles listed below a WiCAN vehicle profiles exists:\n';
models.forEach(model => result += `- ${model}\n`);
await writeFile(target, result);