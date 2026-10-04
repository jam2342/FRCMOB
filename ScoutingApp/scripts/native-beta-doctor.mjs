// Metadata/presence only: does not read credentials, identities or provider files.
import { readFileSync, existsSync } from 'node:fs';
const pkg=JSON.parse(readFileSync(new URL('../package.json',import.meta.url),'utf8'));
const project=readFileSync(new URL('../ios/App/App.xcodeproj/project.pbxproj',import.meta.url),'utf8');
const names=['FRCMOB_UPLOAD_KEYSTORE','FRCMOB_UPLOAD_STORE_PASSWORD','FRCMOB_UPLOAD_KEY_ALIAS','FRCMOB_UPLOAD_KEY_PASSWORD'];
const androidSigning=names.every(name=>Boolean(process.env[name]?.trim()));
const iosTeam=/DEVELOPMENT_TEAM\s*=\s*[^;\s]+;/.test(project);
const nativePush=Boolean(pkg.dependencies['@capacitor/push-notifications']);
const firebase=existsSync(new URL('../android/app/google-services.json',import.meta.url));
const status={
 ios:{deviceArchivePresent:existsSync(new URL('../ios/archives/FRCMOB.xcarchive/Products/Applications/App.app',import.meta.url)),developmentTeamConfigured:iosTeam,distributionSigning:'owner verification required'},
 android:{releaseBundlePresent:existsSync(new URL('../android/app/build/outputs/bundle/release/app-release.aab',import.meta.url)),uploadEnvironmentComplete:androidSigning},
 notifications:{nativePluginInstalled:nativePush,androidProviderFilePresent:firebase,deliveryVerified:false},
 publication:'not performed',physicalPhones:'deferred',
};
console.log(JSON.stringify(status,null,2));
if(!iosTeam||!androidSigning||!nativePush||!firebase) process.exitCode=1;
