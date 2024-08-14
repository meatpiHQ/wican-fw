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

result = JSON.stringify(result);
//Use below line instead for generating pretty json
// result = JSON.stringify(result, null, 2);
await writeFile(target, result)
