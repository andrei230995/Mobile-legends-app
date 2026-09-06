#!/usr/bin/env python3
"""Generate an Xcode project without installing a project generator.
Changing signing identifiers rewrites project.pbxproj, not Swift source or user data.
"""
import argparse, hashlib, json, pathlib, re
ROOT=pathlib.Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser()
p.add_argument('--bundle-id',default='com.example.rankwise')
p.add_argument('--team',default='')
a=p.parse_args()
if not re.fullmatch(r'[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+',a.bundle_id):p.error('Use a reverse-domain bundle identifier.')
if a.team and not re.fullmatch(r'[A-Z0-9]{10}',a.team):p.error('Apple Team ID must be 10 uppercase letters/digits.')
def uid(s):return hashlib.sha256(s.encode()).hexdigest()[:24].upper()
def q(s):return json.dumps(str(s))
objects={}
def obj(key,body):objects[uid(key)]=body;return uid(key)
def refs(ids):return '('+','.join(ids)+',)'
project=uid('project');root=uid('root');products=uid('products')
app=uid('target.app');ext=uid('target.extension')
app_product=obj('product.app','isa = PBXFileReference; explicitFileType = wrapper.application; path = Rankwise.app; sourceTree = BUILT_PRODUCTS_DIR;')
ext_product=obj('product.extension','isa = PBXFileReference; explicitFileType = "wrapper.app-extension"; path = RankwiseBroadcast.appex; sourceTree = BUILT_PRODUCTS_DIR;')
files={}
for path in sorted(ROOT.rglob('*.swift')):
 rel=path.relative_to(ROOT).as_posix();files[rel]=obj('file.'+rel,f'isa = PBXFileReference; lastKnownFileType = sourcecode.swift; path = {q(rel)}; sourceTree = "<group>";')
resources=obj('file.resources','isa = PBXFileReference; lastKnownFileType = folder; path = CoachAssets; sourceTree = "<group>";')
obj('products',f'isa = PBXGroup; children = {refs([app_product,ext_product])}; name = Products; sourceTree = "<group>";')
obj('root',f'isa = PBXGroup; children = {refs(list(files.values())+[resources,products])}; sourceTree = "<group>";')
for label,name,identifier,source_dir,plist,entitlements,product,kind in [
 ('app','Rankwise',a.bundle_id,'RankwiseApp','RankwiseApp/Info.plist','RankwiseApp/Rankwise.entitlements',app_product,'com.apple.product-type.application'),
 ('extension','RankwiseBroadcast',a.bundle_id+'.broadcast','BroadcastExtension','BroadcastExtension/Info.plist','BroadcastExtension/Broadcast.entitlements',ext_product,'com.apple.product-type.app-extension')]:
 build_files=[]
 for rel,ref in files.items():
  if rel.startswith(source_dir+'/') or rel.startswith('Shared/'):
   build_files.append(obj('build.'+label+'.'+rel,f'isa = PBXBuildFile; fileRef = {ref};'))
 sources=obj('sources.'+label,f'isa = PBXSourcesBuildPhase; buildActionMask = 2147483647; files = {refs(build_files)}; runOnlyForDeploymentPostprocessing = 0;')
 resource_build=obj('resourcebuild.'+label,f'isa = PBXBuildFile; fileRef = {resources};')
 resource_phase=obj('resources.'+label,f'isa = PBXResourcesBuildPhase; buildActionMask = 2147483647; files = {refs([resource_build])}; runOnlyForDeploymentPostprocessing = 0;')
 framework_phase=obj('frameworks.'+label,'isa = PBXFrameworksBuildPhase; buildActionMask = 2147483647; files = (); runOnlyForDeploymentPostprocessing = 0;')
 phases=[sources,framework_phase,resource_phase];dependencies=[]
 if label=='app':
  embed=obj('embedbuild',f'isa = PBXBuildFile; fileRef = {ext_product}; settings = {{ ATTRIBUTES = (RemoveHeadersOnCopy,); }};')
  phases.append(obj('embedphase',f'isa = PBXCopyFilesBuildPhase; buildActionMask = 2147483647; dstPath = ""; dstSubfolderSpec = 13; files = {refs([embed])}; name = "Embed App Extensions"; runOnlyForDeploymentPostprocessing = 0;'))
  proxy=obj('proxy',f'isa = PBXContainerItemProxy; containerPortal = {project}; proxyType = 1; remoteGlobalIDString = {ext}; remoteInfo = RankwiseBroadcast;')
  dependencies.append(obj('dependency',f'isa = PBXTargetDependency; target = {ext}; targetProxy = {proxy};'))
 configs=[]
 for mode in ['Debug','Release']:
  settings={'PRODUCT_NAME':name,'PRODUCT_BUNDLE_IDENTIFIER':identifier,'INFOPLIST_FILE':plist,'CODE_SIGN_ENTITLEMENTS':entitlements,'GENERATE_INFOPLIST_FILE':'NO','SWIFT_VERSION':'5.0','IPHONEOS_DEPLOYMENT_TARGET':'17.0','TARGETED_DEVICE_FAMILY':'1,2','SDKROOT':'iphoneos','SUPPORTED_PLATFORMS':'iphoneos iphonesimulator','CODE_SIGN_STYLE':'Automatic','DEVELOPMENT_TEAM':a.team,'RANKWISE_APP_GROUP':'group.'+a.bundle_id,'RANKWISE_BUNDLE_ID':a.bundle_id,'SWIFT_OPTIMIZATION_LEVEL':'-Onone' if mode=='Debug' else '-O','SWIFT_EMIT_LOC_STRINGS':'YES','ENABLE_USER_SCRIPT_SANDBOXING':'YES','LD_RUNPATH_SEARCH_PATHS':'$(inherited) @executable_path/Frameworks'+(' @executable_path/../../Frameworks' if label=='extension' else ''),'SKIP_INSTALL':'YES' if label=='extension' else 'NO'}
  if label=='extension':settings['APPLICATION_EXTENSION_API_ONLY']='YES'
  if mode=='Debug':settings['SWIFT_ACTIVE_COMPILATION_CONDITIONS']='DEBUG'
  body='isa = XCBuildConfiguration; buildSettings = {'+' '.join(k+' = '+q(v)+';' for k,v in settings.items())+'}; name = '+mode+';'
  configs.append(obj('config.'+label+'.'+mode,body))
 config_list=obj('configlist.'+label,f'isa = XCConfigurationList; buildConfigurations = {refs(configs)}; defaultConfigurationIsVisible = 0; defaultConfigurationName = Release;')
 obj('target.'+label,f'isa = PBXNativeTarget; buildConfigurationList = {config_list}; buildPhases = {refs(phases)}; buildRules = (); dependencies = {refs(dependencies) if dependencies else "()"}; name = {name}; productName = {name}; productReference = {product}; productType = {q(kind)};')
pc=[]
for mode in ['Debug','Release']:pc.append(obj('projectconfig.'+mode,'isa = XCBuildConfiguration; buildSettings = { CLANG_ENABLE_MODULES = YES; }; name = '+mode+';'))
pcl=obj('projectconfiglist',f'isa = XCConfigurationList; buildConfigurations = {refs(pc)}; defaultConfigurationIsVisible = 0; defaultConfigurationName = Release;')
obj('project',f'isa = PBXProject; attributes = {{ LastUpgradeCheck = 1600; BuildIndependentTargetsInParallel = YES; }}; buildConfigurationList = {pcl}; compatibilityVersion = "Xcode 14.0"; developmentRegion = en; hasScannedForEncodings = 0; knownRegions = (en,Base,); mainGroup = {root}; productRefGroup = {products}; projectDirPath = ""; projectRoot = ""; targets = {refs([app,ext])};')
output=ROOT/'Rankwise.xcodeproj';output.mkdir(exist_ok=True)
(output/'project.pbxproj').write_text('// !$*UTF8*$!\n{ archiveVersion = 1; classes = {}; objectVersion = 56; objects = {\n'+'\n'.join(k+' = { '+v+' };' for k,v in objects.items())+'\n}; rootObject = '+project+'; }\n')
scheme=output/'xcshareddata'/'xcschemes';scheme.mkdir(parents=True,exist_ok=True)
(scheme/'Rankwise.xcscheme').write_text(f'''<?xml version="1.0" encoding="UTF-8"?>
<Scheme LastUpgradeVersion="1600" version="1.3">
<BuildAction parallelizeBuildables="YES" buildImplicitDependencies="YES"><BuildActionEntries><BuildActionEntry buildForTesting="YES" buildForRunning="YES" buildForProfiling="YES" buildForArchiving="YES" buildForAnalyzing="YES"><BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="{app}" BuildableName="Rankwise.app" BlueprintName="Rankwise" ReferencedContainer="container:Rankwise.xcodeproj"/></BuildActionEntry></BuildActionEntries></BuildAction>
<TestAction buildConfiguration="Debug" selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.LLDB" shouldUseLaunchSchemeArgsEnv="YES"/>
<LaunchAction buildConfiguration="Debug" selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.LLDB" launchStyle="0" useCustomWorkingDirectory="NO" ignoresPersistentStateOnLaunch="NO" debugDocumentVersioning="YES" debugServiceExtension="internal" allowLocationSimulation="YES"><BuildableProductRunnable runnableDebuggingMode="0"><BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="{app}" BuildableName="Rankwise.app" BlueprintName="Rankwise" ReferencedContainer="container:Rankwise.xcodeproj"/></BuildableProductRunnable></LaunchAction>
<ProfileAction buildConfiguration="Release" shouldUseLaunchSchemeArgsEnv="YES" savedToolIdentifier="" useCustomWorkingDirectory="NO" debugDocumentVersioning="YES"><BuildableProductRunnable runnableDebuggingMode="0"><BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="{app}" BuildableName="Rankwise.app" BlueprintName="Rankwise" ReferencedContainer="container:Rankwise.xcodeproj"/></BuildableProductRunnable></ProfileAction>
<AnalyzeAction buildConfiguration="Debug"/><ArchiveAction buildConfiguration="Release" revealArchiveInOrganizer="YES"/>
</Scheme>''')
print('Generated Rankwise.xcodeproj; bundle:',a.bundle_id,'; signing team:',a.team or '(select in Xcode)')
