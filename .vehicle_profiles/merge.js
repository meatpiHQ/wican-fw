import { glob } from 'glob'
import { readFile, writeFile } from 'fs/promises';

const source_folder = '../vehicle_profiles';
const target = '../vehicle_profiles.json'

const files = await glob(source_folder + '/**/*.json');

let result = {
    'cars': []
};

async function add_json(path){
    let data = JSON.parse(await readFile(path));
    result.cars.push(data)
}

let promises = [];
files.forEach(file => {
    promises.push(add_json(file));
});

await Promise.all(promises);

result.cars.sort((a,b) => {
    let first = a.car_model.toLowerCase();
    let second = b.car_model.toLowerCase();
    return (first < second) ? -1 : (first > second ) ? 1 : 0;
});

const resultString = JSON.stringify(result);
//Use below line instead for generating pretty json
// result = JSON.stringify(result, null, 2);
await writeFile(target, resultString)

const models = result.cars.map(car => car.car_model).filter(model => model !== 'AAA: Generic');
let supportedVehiclesListContent = '';
supportedVehiclesListContent += '<!--\n';
supportedVehiclesListContent += '================================================================\n'
supportedVehiclesListContent += 'THIS FILE WAS GENERATED! DO NOT UPDATE OR YOUR CHANGES ARE LOST!\n'
supportedVehiclesListContent += '================================================================\n'
supportedVehiclesListContent += '-->\n';
supportedVehiclesListContent += '# Supported Vehicles\n';
supportedVehiclesListContent += 'For vehicles listed below a WiCAN vehicle profiles exists:\n';
models.forEach(model => supportedVehiclesListContent += `- ${model}\n`);

const supportedVehiclesListFilepath = await glob('../docs/content/*.Config/*.Automate/*.Supported_Vehicles.md')
if(supportedVehiclesListFilepath.length == 0 || supportedVehiclesListFilepath.length != 1) {
    throw new Error('Unable to determine automateDirectory');
}
await writeFile(supportedVehiclesListFilepath[0], supportedVehiclesListContent);