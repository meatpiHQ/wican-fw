import { glob } from 'glob'
import { readFile, writeFile } from 'fs/promises';

const source_folder = '../vehicle_profiles';
const target = '../vehicle_profiles.json'

const files = await glob(source_folder + '/**/*.json');

let result = {
    'cars': []
};

function removeSubObjectsWithKey(obj, keyToRemove) { // Thanks to Mistral.AI
    // Check if the object is an array
    if (Array.isArray(obj)) {
        // Iterate over each element in the array
        for (let i = 0; i < obj.length; i++) {
            // Recursively call the function on each element
            obj[i] = removeSubObjectsWithKey(obj[i], keyToRemove);
            // Remove null entries from the array
            if (obj[i] === null) {
                obj.splice(i, 1);
                i--; // Adjust the index after removing an element
            }
        }
    } else if (obj !== null && typeof obj === 'object') {
        // Check if any child contains the key to remove
        let shouldRemoveParent = false;
        for (let key in obj) {
            if (obj.hasOwnProperty(key)) {
                if (key === keyToRemove) {
                    shouldRemoveParent = true;
                } else {
                    // Recursively call the function on each value
                    obj[key] = removeSubObjectsWithKey(obj[key], keyToRemove);
                    if (obj[key] === null) {
                        shouldRemoveParent = true;
                    }
                }
            }
        }
        // If any child contains the key to remove, return null to remove the parent object
        if (shouldRemoveParent) {
            return null;
        }
        // Remove null entries from the object
        for (let key in obj) {
            if (obj.hasOwnProperty(key) && obj[key] === null) {
                delete obj[key];
            }
        }
    }
    // Return the modified object
    return obj;
}

function removeComments(obj) {
  for (const [key, value] of Object.entries(obj)) {
    if (key==='$comments')
      delete obj[key];
    else if (typeof obj[key] === 'object')
      removeComments(obj[key])
  }
}

async function add_json(path){
    let data = JSON.parse(await readFile(path));
    data = removeSubObjectsWithKey(data, '$ignore');
    removeComments(data);
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