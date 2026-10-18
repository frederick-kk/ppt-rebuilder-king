"""Independent, offline production allowlist packager and ZIP verifier.

No project or dependency implementation is imported or executed. Dependency
payloads are copied as opaque local bytes from a frozen path/SHA-256 inventory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile, ZipInfo
import zlib

ARCHIVE_NAME = "新路径-ChatGPT-20261018-r1.zip"
MANIFEST_NAME = "MANIFEST.json"
MANIFEST_FORMAT = "newpath.package-manifest/1"
ROOT_VERSIONS = {"pptxgenjs": "4.0.1", "jszip": "3.10.1"}
TOP_LEVEL_FILES = frozenset(("SKILL.md", "README.md", "LICENSE", "requirements.txt",
                             "pyproject.toml", "package.json", "package-lock.json"))

# Exact local dependency inventory frozen on 2026-10-18. This is metadata, not
# dependency code. Additional files, packages, installers and jobs cannot enter
# a production archive merely by appearing under node_modules.
LOCKED_DEPENDENCY_FILES = {
    'node_modules/@types/node/assert/strict.d.ts': '33e5e9aba62c3193d10d1d33ae1fa75c46a1171cf76fef750777377d53b0303f',
    'node_modules/@types/node/assert.d.ts': '0fa06ada475b910e2106c98c68b10483dc8811d0c14a8a8dd36efb2672485b29',
    'node_modules/@types/node/async_hooks.d.ts': '2b06b93fd01bcd49d1a6bd1f9b65ddcae6480b9a86e9061634d6f8e354c1468f',
    'node_modules/@types/node/buffer.buffer.d.ts': '3af97acf03cc97de58a3a4bc91f8f616408099bc4233f6d0852e72a8ffb91ac9',
    'node_modules/@types/node/buffer.d.ts': '6a0cd27e5dc2cfbe039e731cf879d12b0e2dded06d1b1dedad07f7712de0d7f4',
    'node_modules/@types/node/child_process.d.ts': '913be43dad2a40e500104700aa26cfb5c51a976114bedf19624de43fdae7474f',
    'node_modules/@types/node/cluster.d.ts': 'e60477649d6ad21542bd2dc7e3d9ff6853d0797ba9f689ba2f6653818999c264',
    'node_modules/@types/node/compatibility/disposable.d.ts': '6c7176368037af28cb72f2392010fa1cef295d6d6744bca8cfb54985f3a18c3e',
    'node_modules/@types/node/compatibility/index.d.ts': '21d819c173c0cf7cc3ce57c3276e77fd9a8a01d35a06ad87158781515c9a438a',
    'node_modules/@types/node/compatibility/indexable.d.ts': 'ab41ef1f2cdafb8df48be20cd969d875602483859dc194e9c97c8a576892c052',
    'node_modules/@types/node/compatibility/iterators.d.ts': '437e20f2ba32abaeb7985e0afe0002de1917bc74e949ba585e49feba65da6ca1',
    'node_modules/@types/node/console.d.ts': 'c2510f124c0293ab80b1777c44d80f812b75612f297b9857406468c0f4dafe29',
    'node_modules/@types/node/constants.d.ts': '5524481e56c48ff486f42926778c0a3cce1cc85dc46683b92b1271865bcf015a',
    'node_modules/@types/node/crypto.d.ts': '4c829ab315f57c5442c6667b53769975acbf92003a66aef19bce151987675bd1',
    'node_modules/@types/node/dgram.d.ts': 'b2ade7657e2db96d18315694789eff2ddd3d8aea7215b181f8a0b303277cc579',
    'node_modules/@types/node/diagnostics_channel.d.ts': '78dbea00e90d2df8ea3dbef0cc379d95b8be9b71cd6bde4c28728f306811803b',
    'node_modules/@types/node/dns/promises.d.ts': '836a356aae992ff3c28a0212e3eabcb76dd4b0cc06bcb9607aeef560661b860d',
    'node_modules/@types/node/dns.d.ts': '4d631b81fa2f07a0e63a9a143d6a82c25c5f051298651a9b69176ba28930756d',
    'node_modules/@types/node/domain.d.ts': '1e0d1f8b0adfa0b0330e028c7941b5a98c08b600efe7f14d2d2a00854fb2f393',
    'node_modules/@types/node/events.d.ts': '41670ee38943d9cbb4924e436f56fc19ee94232bc96108562de1a734af20dc2c',
    'node_modules/@types/node/fs/promises.d.ts': '272ca407e0c9068bdc5152552d876e68037ceae3de62e529306403e973dec8e1',
    'node_modules/@types/node/fs.d.ts': '8e1e46d0a9837ee058c100501080c920fa98081ea3956af0374308ba6f22a33e',
    'node_modules/@types/node/globals.d.ts': '808069bba06b6768b62fd22429b53362e7af342da4a236ed2d2e1c89fcca3b4a',
    'node_modules/@types/node/globals.typedarray.d.ts': '98cffbf06d6bab333473c70a893770dbe990783904002c4f1a960447b4b53dca',
    'node_modules/@types/node/http.d.ts': 'fa7834c715d5357e4540cee40ce96c3250ddb67a7b879a6b7fa0e86d6696f121',
    'node_modules/@types/node/http2.d.ts': '22dfb07a7ab15b66ac043829056fe70124844636ae719551812ac631ba04985b',
    'node_modules/@types/node/https.d.ts': 'a10f0e1854f3316d7ee437b79649e5a6ae3ae14ffe6322b02d4987071a95362e',
    'node_modules/@types/node/index.d.ts': '71d3ae6a5e73ca4130762560425e00984ebaff64d5353a3333d1bb7eb86ef336',
    'node_modules/@types/node/inspector.d.ts': 'e208f73ef6a980104304b0d2ca5f6bf1b85de6009d2c7e404028b875020fa8f2',
    'node_modules/@types/node/inspector.generated.d.ts': 'd163b6bc2372b4f07260747cbc6c0a6405ab3fbcea3852305e98ac43ca59f5bc',
    'node_modules/@types/node/LICENSE': 'c2cfccb812fe482101a8f04597dfc5a9991a6b2748266c47ac91b6a5aae15383',
    'node_modules/@types/node/module.d.ts': 'e6fa9ad47c5f71ff733744a029d1dc472c618de53804eae08ffc243b936f87ff',
    'node_modules/@types/node/net.d.ts': 'a6f137d651076822d4fe884287e68fd61785a0d3d1fdb250a5059b691fa897db',
    'node_modules/@types/node/os.d.ts': '24826ed94a78d5c64bd857570fdbd96229ad41b5cb654c08d75a9845e3ab7dde',
    'node_modules/@types/node/package.json': '234e64edc31580d6052f8abc89b96f156de29d3b4c7121214b960dec09bd2a25',
    'node_modules/@types/node/path.d.ts': '8b479a130ccb62e98f11f136d3ac80f2984fdc07616516d29881f3061f2dd472',
    'node_modules/@types/node/perf_hooks.d.ts': '928af3d90454bf656a52a48679f199f64c1435247d6189d1caf4c68f2eaf921f',
    'node_modules/@types/node/process.d.ts': 'bceb58df66ab8fb00170df20cd813978c5ab84be1d285710c4eb005d8e9d8efb',
    'node_modules/@types/node/punycode.d.ts': '3f16a7e4deafa527ed9995a772bb380eb7d3c2c0fd4ae178c5263ed18394db2c',
    'node_modules/@types/node/querystring.d.ts': '933921f0bb0ec12ef45d1062a1fc0f27635318f4d294e4d99de9a5493e618ca2',
    'node_modules/@types/node/readline/promises.d.ts': '77fbe5eecb6fac4b6242bbf6eebfc43e98ce5ccba8fa44e0ef6a95c945ff4d98',
    'node_modules/@types/node/readline.d.ts': '71a0f3ad612c123b57239a7749770017ecfe6b66411488000aba83e4546fde25',
    'node_modules/@types/node/README.md': '017717668bc0d2d5f6caa72d0cc63a9e3043324ad6b2ca0a2a4d54d68c337135',
    'node_modules/@types/node/repl.d.ts': '4f9d8ca0c417b67b69eeb54c7ca1bedd7b56034bb9bfd27c5d4f3bc4692daca7',
    'node_modules/@types/node/sea.d.ts': '0cb167c371eaa8c869f8a7656a7296f2e4fae43b4d8b803a680236b24794e5f9',
    'node_modules/@types/node/sqlite.d.ts': '0a839dba0287cc0481ad4beedd48a1c64acf1e212ae865d1315f7007ca215161',
    'node_modules/@types/node/stream/consumers.d.ts': '45650f47bfb376c8a8ed39d4bcda5902ab899a3150029684ee4c10676d9fbaee',
    'node_modules/@types/node/stream/promises.d.ts': '37ba7b45141a45ce6e80e66f2a96c8a5ab1bcef0fc2d0f56bb58df96ec67e972',
    'node_modules/@types/node/stream/web.d.ts': '208a6a0bfb227bdf8fb964799d0a206c75cf03ccd72e63bf5495c9331354c6d6',
    'node_modules/@types/node/stream.d.ts': '38dc4655376cd1a4bd6bb3763d92949233e33d38d3dd3cbea7bbf218175a38ef',
    'node_modules/@types/node/string_decoder.d.ts': '18fd40412d102c5564136f29735e5d1c3b455b8a37f920da79561f1fde068208',
    'node_modules/@types/node/test.d.ts': '48a679952eefe4cb776d5a0e1ccba2d3eb53b57448bbb7abc1fcebcbd5440188',
    'node_modules/@types/node/timers/promises.d.ts': '0aaed1d72199b01234152f7a60046bc947f1f37d78d182e9ae09c4289e06a592',
    'node_modules/@types/node/timers.d.ts': 'f0be1b8078cd549d91f37c30c222c2a187ac1cf981d994fb476a1adc61387b14',
    'node_modules/@types/node/tls.d.ts': '2d14da6ecb49bf828d83948765ec2d3a579d476bbb9645e749610baa6ec880ca',
    'node_modules/@types/node/trace_events.d.ts': '66ba1b2c3e3a3644a1011cd530fb444a96b1b2dfe2f5e837a002d41a1a799e60',
    'node_modules/@types/node/ts5.6/buffer.buffer.d.ts': 'a98aedd64ad81793f146d36d1611ed9ba61b8b49ff040f0d13a103ed626595d9',
    'node_modules/@types/node/ts5.6/globals.typedarray.d.ts': '1456e80bd8a3870034d89f91bd7df12ac29acfb083e31c0bb1fb38ca7bf5fbc2',
    'node_modules/@types/node/ts5.6/index.d.ts': '7844b611e67ef2810e907f479dd21c3ffada87473c07602b356af988d43ba290',
    'node_modules/@types/node/tty.d.ts': '7e514f5b852fdbc166b539fdd1f4e9114f29911592a5eb10a94bb3a13ccac3c4',
    'node_modules/@types/node/url.d.ts': '5b7aa3c4c1a5d81b411e8cb302b45507fea9358d3569196b27eb1a27ae3a90ef',
    'node_modules/@types/node/util.d.ts': '5987a903da92c7462e0b35704ce7da94d7fdc4b89a984871c0e2b87a8aae9e69',
    'node_modules/@types/node/v8.d.ts': 'ea08a0345023ade2b47fbff5a76d0d0ed8bff10bc9d22b83f40858a8e941501c',
    'node_modules/@types/node/vm.d.ts': '0aef708fb4c7a6b915e8305cbfac40cd207b032dbaabe9a01889a5fff3254681',
    'node_modules/@types/node/wasi.d.ts': 'ae062ce7d9510060c5d7e7952ae379224fb3f8f2dd74e88959878af2057c143b',
    'node_modules/@types/node/web-globals/abortcontroller.d.ts': '1db0b7dca579049ca4193d034d835f6bfe73096c73663e5ef9a0b5779939f3d0',
    'node_modules/@types/node/web-globals/domexception.d.ts': '9798340ffb0d067d69b1ae5b32faa17ab31b82466a3fc00d8f2f2df0c8554aaa',
    'node_modules/@types/node/web-globals/events.d.ts': 'f26b11d8d8e4b8028f1c7d618b22274c892e4b0ef5b3678a8ccbad85419aef43',
    'node_modules/@types/node/web-globals/fetch.d.ts': 'b52476feb4a0cbcb25e5931b930fc73cb6643fb1a5060bf8a3dda0eeae5b4b68',
    'node_modules/@types/node/web-globals/navigator.d.ts': 'f9501cc13ce624c72b61f12b3963e84fad210fbdf0ffbc4590e08460a3f04eba',
    'node_modules/@types/node/web-globals/storage.d.ts': 'e7721c4f69f93c91360c26a0a84ee885997d748237ef78ef665b153e622b36c1',
    'node_modules/@types/node/web-globals/streams.d.ts': 'd97fb21da858fb18b8ae72c314e9743fd52f73ebe2764e12af1db32fc03f853f',
    'node_modules/@types/node/worker_threads.d.ts': 'ad9bdafb4e7abf14cc53ce7970486a84c87831e62891e5dfe798ddcd55e84701',
    'node_modules/@types/node/zlib.d.ts': '45479f65af737a3e5c26d8772bd8ab1aa8bebbd09c1a808c2b3faddb9b84afbb',
    'node_modules/core-util-is/lib/util.js': '7427f16d9bd9185e409baff3e4b1ed6e3d8dfca84d367f4b8b351eb921618652',
    'node_modules/core-util-is/LICENSE': '33b734d60042d0fe0c92dd1fc1e874193a1c899ec3e276a2eb935d2d0bf5b710',
    'node_modules/core-util-is/package.json': '0067bcd4ef1c86da02a45ad770883b39a9d14aa0b00113071609d5fb3dce0bc0',
    'node_modules/core-util-is/README.md': '57072548a79553ec5a930b370f5f37f2b86f57ab39357dc63e097c7e8ba7412a',
    'node_modules/https/package.json': '30cc493ed51f008e9e1d0d0730759183294f9d1b39798032ee341dc6a17399f7',
    'node_modules/image-size/bin/image-size.js': 'd2aebacfafe6a18aa0dbe5e223e502e2d944a2715733afa9aa34e58fb07b125d',
    'node_modules/image-size/dist/cjs/detector.d.ts': 'ff93a5aa4809114fb4588926811b02cdd6af45a53eda27507d95f2c15bc3cbc0',
    'node_modules/image-size/dist/cjs/detector.js': 'ebeaa7801c43c181e54e3f6b24d3d5e561a6cee9556badc4459bdeacbe564a9e',
    'node_modules/image-size/dist/cjs/fromFile.d.ts': '55c86e7cf6573931fdadccab6f7e26e67c3c74a2a867999ff58db3a7754bf62d',
    'node_modules/image-size/dist/cjs/fromFile.js': '6bc7065cca700eb1070e0aa6971164d3c3741039ea936307c19b3dfb87ede5f6',
    'node_modules/image-size/dist/cjs/index.d.ts': '1644e47d6a60305461f228a44d586eb3469588e937f047c172663d54d0d1e751',
    'node_modules/image-size/dist/cjs/index.js': '9cd6dd0a7e1205fb9c8a057b2bb6af419b8af9305507dd60e5bf5653c7939984',
    'node_modules/image-size/dist/cjs/lookup.d.ts': 'c3b176e0e824362c64bb2f4cadab04c9fdeb2d38acbd7a6406b9da8dbc8d050e',
    'node_modules/image-size/dist/cjs/lookup.js': 'cb4dee5d6b0e4d279fcdc658012cc54ea2dac97554ecc0a54bf59b79c327fb57',
    'node_modules/image-size/dist/cjs/package.json': 'fa6944a20ca5e6fbaf98fd202eb8c7004d5b4ab786e36b9ed02ee31dbe196c9f',
    'node_modules/image-size/dist/cjs/types/bmp.d.ts': 'd402c0caec8e48bbfe5b1a2a14156b8f070bded00419d678d2d7791a13dab1e8',
    'node_modules/image-size/dist/cjs/types/bmp.js': '40074f59699973926472745a030bec9f423bd0a2adeff6d475989317e95d0035',
    'node_modules/image-size/dist/cjs/types/cur.d.ts': 'f7678722e29a8ad1b5d72dda0f65b9e0fccc43c5e5b5b9606ed603fef4131e55',
    'node_modules/image-size/dist/cjs/types/cur.js': '87bfe99081c318a8be832ce515e9f791c1a329bdc68102bbb606237fe0077966',
    'node_modules/image-size/dist/cjs/types/dds.d.ts': 'd8b072c570f439228d2cbcaae2356bde14a577f94430289411872445aed08115',
    'node_modules/image-size/dist/cjs/types/dds.js': '4c9f8bdccd669fd833ca9291004ea88ed2b3989eced1b95f76373df6654e87c8',
    'node_modules/image-size/dist/cjs/types/gif.d.ts': 'c2c6318ceaf2b0ae4bce443f509fcd5d2ae478d43a197bd13fe5b96893eeba21',
    'node_modules/image-size/dist/cjs/types/gif.js': '653f6497f86153d0113eebed496925e85ecd804abf026ffb7eccfaf1831614f2',
    'node_modules/image-size/dist/cjs/types/heif.d.ts': '00e6762a319f96fa05e2fbd40bf2a5bfaa3ae9907f47ef0c7fc44a2da3a83978',
    'node_modules/image-size/dist/cjs/types/heif.js': 'd98f45d1c8ed88c1ba6343c98cb108e979b9150817b1c81de427ccd63d72f8a7',
    'node_modules/image-size/dist/cjs/types/icns.d.ts': '893e827157db27bc016ff8cab66256f753c5ea480244db5e48fd058d5eb2d102',
    'node_modules/image-size/dist/cjs/types/icns.js': '7c82452f93b474acda2ae3797600a38bed8f564d39e8c43e0cb493b778aa43e4',
    'node_modules/image-size/dist/cjs/types/ico.d.ts': 'de727a540995404ea4f9a9fcd095824564d25a06b3bee49e3229d1dce57a86bc',
    'node_modules/image-size/dist/cjs/types/ico.js': '55ab89db7c2d636b9cd9fb5cfe7e8313d91fe825491ba8a6fb7f3dcc171259c3',
    'node_modules/image-size/dist/cjs/types/index.d.ts': '60605b7d972bdad92c5b418d2045ba6691aa74514d37d5ab435bff87f559811b',
    'node_modules/image-size/dist/cjs/types/index.js': 'f1f9c8360e16970164414dbddc913f17d8b2870bcee79244012996f94378e8de',
    'node_modules/image-size/dist/cjs/types/interface.d.ts': '492777e895c60113cc89342f6922c3c3b4730e621aeb90cd9f34dbc4f430c681',
    'node_modules/image-size/dist/cjs/types/interface.js': 'd43aa81f5bc89faa359e0f97c814ba25155591ff078fbb9bfd40f8c7c9683230',
    'node_modules/image-size/dist/cjs/types/j2c.d.ts': 'f3a111d106347b88a0453b6c2d99c1fec19aa69ce17f15aeda3c4ae07a6e8e4e',
    'node_modules/image-size/dist/cjs/types/j2c.js': '976fd7968a2c7a99c1fd3410c10ceed4661432b96851cda08b7fd84b6e82df73',
    'node_modules/image-size/dist/cjs/types/jp2.d.ts': 'ed1c73f4ae3739f0ead6a7283931da8fb094b3c32c1b0b00bc256782c587afb1',
    'node_modules/image-size/dist/cjs/types/jp2.js': '888bcbf4fd302ede7d68459530ca7d5a9bf50263f7cfe9c45a20a01dd651f42b',
    'node_modules/image-size/dist/cjs/types/jpg.d.ts': '35691e676d56d35179d5ceb23f769a75718279cbb11ddc186649fa6f86ed3c93',
    'node_modules/image-size/dist/cjs/types/jpg.js': '6d0fddf95c16e17c558c6783d5e487a6e7298bc7f7887ea121b2f2fc7374bde0',
    'node_modules/image-size/dist/cjs/types/jxl-stream.d.ts': 'd728c700c6191a29b9762a7477a8405a99d631d21fecd8260765d03a50dfb83d',
    'node_modules/image-size/dist/cjs/types/jxl-stream.js': '6fc27af878579dc3f07c92919d41e0ce5d2efe7da1f70163dcc2ef7694996563',
    'node_modules/image-size/dist/cjs/types/jxl.d.ts': 'e7c867ad1f94fa239913aba1ef2e76563d33db12e6c56c2e063dda785d6e7c11',
    'node_modules/image-size/dist/cjs/types/jxl.js': '9f745629533dc9490cb3a152ca5bd55c2fad965a7154952b5a06b294f8d99879',
    'node_modules/image-size/dist/cjs/types/ktx.d.ts': '97a49999c81f73d1ecb3831734b5d6c344d27cc84ddb40861de0ca8c3683faa2',
    'node_modules/image-size/dist/cjs/types/ktx.js': '8cc90cd8a124b93dbc5b5c5bfc9aef6cf3a4b7e1052f61e62dde9b217f4390b1',
    'node_modules/image-size/dist/cjs/types/png.d.ts': '8ccde5ced98871fbed09bb29f978a39d97e983bb1474165cb014240206abfc9f',
    'node_modules/image-size/dist/cjs/types/png.js': '1e098075624f99cb51fdfe5e99d593f4176fe586065ecb51d91d4e4e0ede4f5d',
    'node_modules/image-size/dist/cjs/types/pnm.d.ts': '9a5a7b77ef1b3417aca17ddb5c47100b4efe5ee0b2643889df2820729744de1b',
    'node_modules/image-size/dist/cjs/types/pnm.js': '7cd9009f4e14a89b390e63496340042b8d47b4110e86fe828963f97e1f8eb31e',
    'node_modules/image-size/dist/cjs/types/psd.d.ts': '726ef936e6030a6c64d87001042bf15cb4c5eed9de94183928f0c2e3d598651f',
    'node_modules/image-size/dist/cjs/types/psd.js': '5fbd312554575b60eceaaeac74441ed3dfedab5f03820a26d5f9432af0ad2434',
    'node_modules/image-size/dist/cjs/types/svg.d.ts': 'b3a8d42ce5b097f57fca84da5622017012096203486bc96cbf0dc9aa1962071e',
    'node_modules/image-size/dist/cjs/types/svg.js': '61740559796ccbfab2327b9d1c66d113d2463ecf4dab62a1f77c2b112d345e4b',
    'node_modules/image-size/dist/cjs/types/tga.d.ts': 'c1675b34e0bc3d27fc213bc6217f669245d61d5170cf8bcabe611b344ffba151',
    'node_modules/image-size/dist/cjs/types/tga.js': 'e8196030880d13558cd53d56b9b0f7420cd4321b4f60d669b1360398d824b1b2',
    'node_modules/image-size/dist/cjs/types/tiff.d.ts': 'ed9337a3ff096c32c6aec68cdca1134c8816ce05d9358c1364eb5811b5b3e9c5',
    'node_modules/image-size/dist/cjs/types/tiff.js': '75ac1f5b4253106ef1df3384996ab61fad6185de48105f0fef30d4015034bff1',
    'node_modules/image-size/dist/cjs/types/utils.d.ts': '3eec062deaf76d7ebb27c908a3e9ab048cb7a43b9222e9861e1524040a8d5a0d',
    'node_modules/image-size/dist/cjs/types/utils.js': '142c9dda19727b67aea37e82d245c2820eb252cc46502088ffaa5379a08ac55d',
    'node_modules/image-size/dist/cjs/types/webp.d.ts': 'a11b63684aab0b86cb06c39e1f0cf31592a7f5510730696be7d79b2938d287e9',
    'node_modules/image-size/dist/cjs/types/webp.js': '734a4316e31b3d6fed01d2fc2a7a59e266a61159a4275d3d1f5c4440956049ff',
    'node_modules/image-size/dist/cjs/utils/bit-reader.d.ts': 'e44cf04436b4c3ec8364db916f966da539982d9d0070a9e5bb779219fc84b9ec',
    'node_modules/image-size/dist/cjs/utils/bit-reader.js': '486e8000ad0a45db514c86e653becb4e337aa501064494e57211ca4a1db07544',
    'node_modules/image-size/dist/esm/detector.d.ts': 'ce78b056957826fc8bb8cda412805d6b9c43c864befbb8bd3af719222403467a',
    'node_modules/image-size/dist/esm/detector.js': 'bb746cfe02b915c3a7236c8283200aab654b2f2a72c7c23d1d5a2b76f063488f',
    'node_modules/image-size/dist/esm/fromFile.d.ts': '2eed96bff9902d94ea1d6698e591c903cedf4ba3cf9f747559a95d0bb59a4b5e',
    'node_modules/image-size/dist/esm/fromFile.js': 'ecb0f04b4c2c9abafd4a4daf7a77a31002952df63244232c27609494f6e9cb18',
    'node_modules/image-size/dist/esm/index.d.ts': '2f75fe05d91bcb51f70b1da032508537274fb8adb8e1dd2e4fcc6ab5bd27866c',
    'node_modules/image-size/dist/esm/index.js': '2f75fe05d91bcb51f70b1da032508537274fb8adb8e1dd2e4fcc6ab5bd27866c',
    'node_modules/image-size/dist/esm/lookup.d.ts': '5764fc38e58d95dc37cbd78673daf600f69142b7229f1e158c0dabb604b5e827',
    'node_modules/image-size/dist/esm/lookup.js': '114187e7a9f5d8bd10a3e007dd1afd387934690baf0b21deea570b9328bc9b4f',
    'node_modules/image-size/dist/esm/package.json': '6dc1b06d6b093e9cccb20bee06a93836eee0420ae26803ca2ce4065d82f070d1',
    'node_modules/image-size/dist/esm/types/bmp.d.ts': '179ea8cc7075ee5e1667a72910b45bda5d0e0dcc9c171fbd453b085cf877aa6f',
    'node_modules/image-size/dist/esm/types/bmp.js': 'ec93e9d3bb24f65d6f71c4445af7c95ed9930a6910958966700babcf373d59c2',
    'node_modules/image-size/dist/esm/types/cur.d.ts': '82a7ac59bce441e6d3de47e751c7259a20ec18c66fbbe73e2ecb6156efb1dc1e',
    'node_modules/image-size/dist/esm/types/cur.js': 'bf45970d96fc868dcb2cfdca07f88a212c0cff4b38d0cc9808bcc2d8493f4387',
    'node_modules/image-size/dist/esm/types/dds.d.ts': 'ec1d5317d3e8457abbeae96aee95db2aaa68118a6f7b21a4e0d269267a00553e',
    'node_modules/image-size/dist/esm/types/dds.js': '39bf17a10c57a7be53db373b0a609beeb7f3d01221c4dd82ef34f2b35e23e697',
    'node_modules/image-size/dist/esm/types/gif.d.ts': '60d8e67f6f07e7f2fc07393508befce043cc9a5ecdb2a7e3f709507ceb03d586',
    'node_modules/image-size/dist/esm/types/gif.js': 'e21e0c838e6f1467b6fb037cd71a85c327e2a0d9143e240afb8036aabc8d2f36',
    'node_modules/image-size/dist/esm/types/heif.d.ts': 'c18d2346932feefb1381c041e75a91eda99134f6d9cba69fb7a57286a6834d7f',
    'node_modules/image-size/dist/esm/types/heif.js': 'a638796705e37aee4f35223b0b05e86e9c8be6e4d4fedc9aae04e5ac45dda46a',
    'node_modules/image-size/dist/esm/types/icns.d.ts': '67decce0d0c274e99444dcac046ee887b958572f1ea9954da5df33104ea73a0a',
    'node_modules/image-size/dist/esm/types/icns.js': '15e29c84cb58b3bdffc9d5fdad70e55a2f1a810ec3601936dcdc9dcad7740fae',
    'node_modules/image-size/dist/esm/types/ico.d.ts': '5af9f3b1000bfd88e8fccda1f7c2b65f282695b239d89cf091a0c09e98b72768',
    'node_modules/image-size/dist/esm/types/ico.js': '320f18652e6e1eda37e5bb9d066feb93171f23a073fc401739d012ea031ac9be',
    'node_modules/image-size/dist/esm/types/index.d.ts': '83eda7eec1715e8e138f44744ed33b587e291134b7e52f855e21a10b45038f7d',
    'node_modules/image-size/dist/esm/types/index.js': '3504961ac0bc518963e0f89aa9185d76be864585cd93fc1ece2c35d3f8f9bfbe',
    'node_modules/image-size/dist/esm/types/interface.d.ts': '492777e895c60113cc89342f6922c3c3b4730e621aeb90cd9f34dbc4f430c681',
    'node_modules/image-size/dist/esm/types/interface.js': '8e609bb71c20b858c77f0e9f90bb1319db8477b13f9f965f1a1e18524bf50881',
    'node_modules/image-size/dist/esm/types/j2c.d.ts': 'c588ab16ccd83ce69588f63ac4763c8afad356396701f823728754b6b896450c',
    'node_modules/image-size/dist/esm/types/j2c.js': '1d323a31588b7e9dabe07868894a0b2fbb04acaaef61a295ddc1a64816f8f4bc',
    'node_modules/image-size/dist/esm/types/jp2.d.ts': '2f40e1950edc6b87a04b82ae37f7f196d0bac43f2f82bf47caf46bbb44f6ba84',
    'node_modules/image-size/dist/esm/types/jp2.js': '44f6b2e99992a0b982a6db8cf1f5a013c5517b4d15567ea341df45dadf477564',
    'node_modules/image-size/dist/esm/types/jpg.d.ts': '0342823f6ed1bd60faa25b576f04cb857ad4b492975160b16016afc5e9deb3df',
    'node_modules/image-size/dist/esm/types/jpg.js': 'd831935b8f549074fe3d2e715da91226f27832759ef843fa6f90e42f6e5d2a0d',
    'node_modules/image-size/dist/esm/types/jxl-stream.d.ts': '79b19b1d706b9bedda486cd41bd43649e49ab3e891eb65ed417b0071856cb731',
    'node_modules/image-size/dist/esm/types/jxl-stream.js': '1699a758f7adc7c935a21f6fcf54f71c9158e6060a4804f1407add4403fd2afd',
    'node_modules/image-size/dist/esm/types/jxl.d.ts': '50990e7eb856fee636c27b4004d9a40d38dd89a6680addcf98887293412de9a9',
    'node_modules/image-size/dist/esm/types/jxl.js': 'dddef951cb3f16a5c5a71928e179fd89a920e2f54de9e2bca78156e016c83d55',
    'node_modules/image-size/dist/esm/types/ktx.d.ts': '670e2684004c312f6d0106858f1e2c438db0487ccea32442af73d4fa1ab2f7cc',
    'node_modules/image-size/dist/esm/types/ktx.js': 'eb7f9d9eb105cbcfd70c5b1078f1d91e1d5c535fc9c0edb4bff9ad964e8d4b52',
    'node_modules/image-size/dist/esm/types/png.d.ts': '6e8549c2d48f53c01585b9ebb6540fa9f66088c6972ff0a835f5f26c40da3761',
    'node_modules/image-size/dist/esm/types/png.js': '97db33688f1ed253e6058a8ea8efa1f1a563ecb4d3d4041557a6f28141a8f669',
    'node_modules/image-size/dist/esm/types/pnm.d.ts': 'e2c646d232e4db2f4ee49770744acdddbb8ba81997c2e9a0874ed8e80e413e9a',
    'node_modules/image-size/dist/esm/types/pnm.js': 'ba30db1ede491005ded3783544268fd45f73c13554147993c997fe4510d06fd5',
    'node_modules/image-size/dist/esm/types/psd.d.ts': '152e9643ab826d7bb796323b755089d5704013bebff779f21091c9c4ff8ab4b8',
    'node_modules/image-size/dist/esm/types/psd.js': 'fbc97bbc876163d066fe5a74bff81c0c67c368f53b0f17bed556a6b55f5f6095',
    'node_modules/image-size/dist/esm/types/svg.d.ts': 'deda06fcae9224e699415c19c1ddb70566c624ad48d53fa3c7eea63baef256f6',
    'node_modules/image-size/dist/esm/types/svg.js': 'd9476103b394b7bf14d12a884814ec0a04f4f0d29d888db91da9cac78512a67d',
    'node_modules/image-size/dist/esm/types/tga.d.ts': 'cada319f70cd1324fe97c06baa9cd07770a91724407f47c15b26323a085c64a1',
    'node_modules/image-size/dist/esm/types/tga.js': '8db5033ec0610ea79f5ae9f2628da1bc29cd54457f4cb7b8b909e3f1ffb3cac0',
    'node_modules/image-size/dist/esm/types/tiff.d.ts': '4cc4ffca5a3c55e564e2816184313e685a1ee4f22e40b99e830b146d95e54730',
    'node_modules/image-size/dist/esm/types/tiff.js': '09ed463604deaf9192f9ec155585e1550363c3df7b5e52e55f29764a0ee6143f',
    'node_modules/image-size/dist/esm/types/utils.d.ts': '3eec062deaf76d7ebb27c908a3e9ab048cb7a43b9222e9861e1524040a8d5a0d',
    'node_modules/image-size/dist/esm/types/utils.js': '7fec2bc58e713db3e30bdc2826c27c3f54ce92bc33f2e98994474f81b8b6dcf4',
    'node_modules/image-size/dist/esm/types/webp.d.ts': '240a7158c6fca6d3c082272e43ad379495ab133c36e8a136f4daba917c1ef2ed',
    'node_modules/image-size/dist/esm/types/webp.js': 'c77e6fdd2c6a93fe1c29f15834b6a853fcd8a80ba3df92ae7c4a23b633720202',
    'node_modules/image-size/dist/esm/utils/bit-reader.d.ts': 'e44cf04436b4c3ec8364db916f966da539982d9d0070a9e5bb779219fc84b9ec',
    'node_modules/image-size/dist/esm/utils/bit-reader.js': 'bd5edd2d06526580ec227583dd7f8fff915541a04546a8765bd19ccb47a30f2e',
    'node_modules/image-size/LICENSE': '3109783dad7527b490aabc49c3cb148573c19b41b843e70cf9388a93f3d7fccd',
    'node_modules/image-size/package.json': '6a7f6e5afc20fec03227ca02b86d24457ecd29dd44d725f54bf4d91d9e917c0b',
    'node_modules/image-size/Readme.md': 'fa22045604f4affe795c35221cdb138b91f9645e2b6860e36963bf9d54cc520f',
    'node_modules/immediate/dist/immediate.js': 'f7d30178cb60471a7e90378c20453634b2ffb5f8ade5c8f584ae0ee0845fb3ac',
    'node_modules/immediate/dist/immediate.min.js': 'de5e482a8b02c65c727c105b42888da53e25b9fe5d815afbadc03f4d193df7d4',
    'node_modules/immediate/lib/browser.js': 'a736ab3e296a0637d040603df55a5943d1f850e51c94f8595965c4875e307512',
    'node_modules/immediate/lib/index.js': '285c9e2ca232f5d298816680d9f5a29c455daa56356ee3566b556e5dafeb9427',
    'node_modules/immediate/LICENSE.txt': '809e66de579fb7d92503848bbe6d1f348f4f6521a71e5d294fe7cc0c87a3b4ff',
    'node_modules/immediate/package.json': '1e40d711ce5348d237f7a2093d1707ca7c34fc56369f6be10c8e2dc9bcae0a96',
    'node_modules/immediate/README.md': 'f8ba840a9e01f16ccab189fc52191a42324153ad376b8ade110db8af9300ffe1',
    'node_modules/inherits/inherits.js': 'bb380f32bef5feb18678f0f45f88073fed5d7a0069a309132cb2080cd553d5c7',
    'node_modules/inherits/inherits_browser.js': 'ad322a7b1dec60f3d2ebda2091816469efb55b567d241cf3cf0fa4c5a4afe500',
    'node_modules/inherits/LICENSE': '5ffe28e7ade7d8f10d85d5337a73fd793dac5c462fb9a28fbf8c5046c7fbca3b',
    'node_modules/inherits/package.json': 'be645800bc94fd8de29c8ae91690549b316cc437100108aeea7b2f347693cc80',
    'node_modules/inherits/README.md': 'b33c8be1ca837d9e4c9b0e04a550feacbc101556164d3cdbb875aed6ccbb8a29',
    'node_modules/isarray/component.json': 'f9e5ef95d8e8f65a5dcd3a200b38e5a13461ed95114dac053d908c391c12d731',
    'node_modules/isarray/index.js': '9b8c691372802da788c9c5f4e1ca2f1ed0b88ab8722176c2aea15e38ec86d249',
    'node_modules/isarray/package.json': '93165ce56e458216c18240cd961a522af5b18e51da06f55d88ac552234455d95',
    'node_modules/isarray/README.md': 'ff138e683771b187f3629c383db72ee7d632009010a36d08e18e8d2a34222ec7',
    'node_modules/isarray/test.js': '46997dc4554dd5a554b4883a9c13180c628bb36baadf8f89d2db5861f5239f44',
    'node_modules/jszip/CHANGES.md': 'dfb792e5a1bf94399d7c11227197158d9851d64520f400ac9848435364fc64f6',
    'node_modules/jszip/deps.js': '35b1b517365d5935151333ef8b57048261b79573b765e96e5ba69aa0cc05a8bb',
    'node_modules/jszip/dist/jszip.js': 'e2e1be5c22d3f15b0b89e676d3eede94644529996d9898a6b1587c9908a78506',
    'node_modules/jszip/dist/jszip.min.js': 'acc7e41455a80765b5fd9c7ee1b8078a6d160bbbca455aeae854de65c947d59e',
    'node_modules/jszip/index.d.ts': '522cb15ff9bef5a65c2f3dbd10dbba9e7ecae4de32f90f5c0b4198132be63ae4',
    'node_modules/jszip/lib/base64.js': '5df6b73d9ed6c317c67835f3427d860c16fd93acf503eb471fd897167e747fdb',
    'node_modules/jszip/lib/compressedObject.js': 'b91f823d32f39cda4002bc0954993f20ffea6747d42109253b7d6347b4abb31b',
    'node_modules/jszip/lib/compressions.js': '50e650a7eaf2a54ede299c12dfba30a362c7644e7c00df0d16ab3cf228bb77b0',
    'node_modules/jszip/lib/crc32.js': '63750f4131b1eabb545a4718c863af860184baf033c76851be282f1c7f5dcba9',
    'node_modules/jszip/lib/defaults.js': '585eeeb04375fe7bd27e846d8941d26fd435efb36a87fba9ff137fd8b97880a6',
    'node_modules/jszip/lib/external.js': '982fed36a600668223d1d0ce8c446f87ccd1f82634df18f206b4a2f4096e31c7',
    'node_modules/jszip/lib/flate.js': '57f1452680cf7af6a9659de398d847dd1e870ea3a601662c498b073c640eb547',
    'node_modules/jszip/lib/generate/index.js': '19f13a6ff615464657aa5b8651e669950c633095cea37d3179a51d8c645adb3b',
    'node_modules/jszip/lib/generate/ZipFileWorker.js': '907d84ea582def3954aade073444f398bef6edf1b931084da4a77bb4dc26c1c8',
    'node_modules/jszip/lib/index.js': '0fdb844fcdd6fd8ce8ced5e20b37cdabb629b4c155a1d3fb5df29c69eeb4f228',
    'node_modules/jszip/lib/license_header.js': '27873abeec2df3693480a8d1b8fe17dd61bee843d1b39c1c9cfab57ffe93099e',
    'node_modules/jszip/lib/load.js': '7e3181c7669cfc584f29f3060cdf210021ec5c723c36af039fb5433de52d25da',
    'node_modules/jszip/lib/nodejs/NodejsStreamInputAdapter.js': 'dcf35c72168e825d2e634cfa13a289ba8c7a76da0584a39b785611d0d736ddd9',
    'node_modules/jszip/lib/nodejs/NodejsStreamOutputAdapter.js': 'cc88c3a8e6aa74262bd1ecc452ee8f78c48a16a0b6095d2f9df7500320957799',
    'node_modules/jszip/lib/nodejsUtils.js': '763d0af1cde619da6d9cfbe1ee82e2e32d86349d8abe32613c7481e8143740e1',
    'node_modules/jszip/lib/object.js': '51f8d689e02f62837edeb449866238cf03df7d779d260d19a683abee27651273',
    'node_modules/jszip/lib/readable-stream-browser.js': 'ed51f0a96964f61d5700b6c681b055dd07e27140f1b719d2fe1c64fd14b506e0',
    'node_modules/jszip/lib/reader/ArrayReader.js': 'bb711509472587e8c88e8e311d5c86bde76528286793afbc6573c1ddd9674814',
    'node_modules/jszip/lib/reader/DataReader.js': 'e8e0b4728ddc6fc3dbe5b449d87b884c4b3be99862bd1edf6a700ecfb3b3b247',
    'node_modules/jszip/lib/reader/NodeBufferReader.js': '1703f32f1cba687ac630690901848ab07a1b6a06b0e8fbe88a8ae6f5a67da84d',
    'node_modules/jszip/lib/reader/readerFor.js': '1eef5a73bba844922812893f9a7e01cfdb54072aae42f7ecf67c41dc925ea9cf',
    'node_modules/jszip/lib/reader/StringReader.js': '477a2e887eee11a56a50726152327b2a4d75d778f96463933197706e1ce90d59',
    'node_modules/jszip/lib/reader/Uint8ArrayReader.js': '198816389bc1c62b3ca7c5c6f5fdff1b076f83e5bd74b37ee5b504e6d7e155d9',
    'node_modules/jszip/lib/signature.js': '18e10348490604003a2480aea813a6e90eaaac26c050d559c281618b9aaded56',
    'node_modules/jszip/lib/stream/ConvertWorker.js': 'ae1dc8573bab7e5be4a4cb9958201e3e763cb2f2392a04d9a8dc440d683042f0',
    'node_modules/jszip/lib/stream/Crc32Probe.js': '8e186e5e0ab42fff35907666a90266420751613c28266e5809b7bfa2d55a3a9a',
    'node_modules/jszip/lib/stream/DataLengthProbe.js': '638e65360b0fbe1f4e0b2628fd3a1782a4df59c477d454007fa52b1a2587b93f',
    'node_modules/jszip/lib/stream/DataWorker.js': '8203f14e856d33aa9afc272a576f169b29980e8da0ee0f633e6e5f23cfa48a3e',
    'node_modules/jszip/lib/stream/GenericWorker.js': 'ae76cecd1d728ca41dd6ae0665b258f442da64d9b25ff94785dcb80a501b8be1',
    'node_modules/jszip/lib/stream/StreamHelper.js': 'aebc6518b4871d106e799f90139d55620d02e9413ef2a7b0f953a9cea7bda124',
    'node_modules/jszip/lib/support.js': '7629a6ef610030595e4c2211249f487e44cfbe4b8299105d64b5491d87fd3a51',
    'node_modules/jszip/lib/utf8.js': '5bdfe3666fe57640bc791b4d6d2fc00853f4d954596260138bf488054fa9df5a',
    'node_modules/jszip/lib/utils.js': 'c77e896a1e47b44939e9dcdc2e05275fd83c1d69ee252089f9a1f44ba8c5cd1d',
    'node_modules/jszip/lib/zipEntries.js': '0891a1a82e557c64a51893a5ad70dec76f6b5c91170047ab28617286c96c6ec7',
    'node_modules/jszip/lib/zipEntry.js': '105b8cec00daa3852f92371b3898d6eab52eeb984113ba8958d09bce701e34df',
    'node_modules/jszip/lib/zipObject.js': '18871af92fac75d015ba7d4d3105c0746839ab089f78670cb86d76bfa53b1ce6',
    'node_modules/jszip/LICENSE.markdown': '566c953c6090b1218ca6217dd7359d45dde46581968586dc607d59a78af6a9c4',
    'node_modules/jszip/package.json': '83569b5b130b5ec27ae281ed80420ad364baf1bf64cb82e6ac8fb891e1b4ec4c',
    'node_modules/jszip/README.markdown': '5eee3659204ed8f04b58d388b2dae55539c5cbcda733e4e945811c9121a68ede',
    'node_modules/jszip/sponsors.md': '05023a2cdb8fbddf99c368ce364e008f99d2b8da90ce5aec67d2ad633665f672',
    'node_modules/jszip/tsconfig.json': '513defa2eb1fc4aed9407b2b08fb21ab1c9c54b043609fe6c2b344891392606d',
    'node_modules/jszip/vendor/FileSaver.js': '67421e50f3ff4c97f1c3fa3749bf071cc57357c0da97783f4f8fe3affa3a52cf',
    'node_modules/lie/dist/lie.js': 'd3fc34d3a5a96c7294ad76eaa93bcf89786abc82647cf4f30306e007b2c7aded',
    'node_modules/lie/dist/lie.min.js': 'fd749fa138f78ba1f85a3260300ce67e7e7cb6f02085dcb51081ad68a522f4b3',
    'node_modules/lie/dist/lie.polyfill.js': '3eb9c37db77565e5843ddb6e6c9f6d6056916c30a5eac0fcc59aa680540ac9ec',
    'node_modules/lie/dist/lie.polyfill.min.js': '69bfdc3b86f44244316f7b97ec418e7763a129dc603ea4ea7b2e00802a5f6c6f',
    'node_modules/lie/lib/browser.js': '512e8898e512d2029d12067b8e059a9733fe7e43d0ea98f532522d34582e3e59',
    'node_modules/lie/lib/index.js': '9408e5a97d3e8a0b14c96aef888e9a1f0b7358fd22c4ee33e9321cc950ade9dc',
    'node_modules/lie/license.md': '5c81b0caa98593408b03125efa25efe622341ed87ae55561968828cd887d64a4',
    'node_modules/lie/lie.d.ts': '0d433663211c9a094928624c7418ed10d543051c575376687dce2bcd6b54cef8',
    'node_modules/lie/package.json': '3faafb3b558146534b1849e6d2ed8a9c9c88eff27ef868827d8de17b979cf701',
    'node_modules/lie/polyfill.js': '9fdfb4a0bb2e0ed1fb340bbfcc6fc42fbab1dc8420e3ca217fbedaf2c82c1230',
    'node_modules/lie/README.md': '97a1e33c510ea9ce0eb1faf0bc1f89a71bfe414c1768b227d47b5ac3c50e1a29',
    'node_modules/pako/CHANGELOG.md': '8d3b2a5ff30f052a33ff5a2a0d32fea9c22228a2eff08deb76856eac3bd31090',
    'node_modules/pako/dist/pako.js': 'f407f7f15b7a0fc3af813a8cc526869e088c70801cadc31132a195fcb6f7b133',
    'node_modules/pako/dist/pako.min.js': '29cfa0542b9864b6640cfdcc8f15b184db6431b532db2702a3ce97e5f2a7fc1c',
    'node_modules/pako/dist/pako_deflate.js': 'da1738ff3bc96b16f25390d329f4cf90f409f73c2a3fc9ef00a14fa0240d7f9f',
    'node_modules/pako/dist/pako_deflate.min.js': '2494cd81fab386095b70c85be95e06432733f828c4ba02f940ed05c54087114b',
    'node_modules/pako/dist/pako_inflate.js': 'ec7e5936c652800e132c63cc1fcd00c23103a6a10a294ed3736bab70e38fc592',
    'node_modules/pako/dist/pako_inflate.min.js': '2ca27e9a8dae569cdeac42752ed1aed1afeff7f19282d3cc12c0aaa54a08bc04',
    'node_modules/pako/index.js': 'e7ec4e35d94d01a2e4ee5dca62b8fb08ac7411596edb54b398651f4eb563561d',
    'node_modules/pako/lib/deflate.js': 'a19a5dee5ac13a5bec54c15629bcd3449a0a7f15410d0ab582c1efc797a01bb0',
    'node_modules/pako/lib/inflate.js': '18b7dd3b012a1105f3280737107a5a6f32a97c7036849dba7bd1f1e54cda9f53',
    'node_modules/pako/lib/utils/common.js': '6e7878274f7e0f42e7cfb379933b447df3045b9139501f797ed29adf1affde14',
    'node_modules/pako/lib/utils/strings.js': 'bf827ddd52c2e998196adb640ae1192f7dc49ce5e6e5926e86198feeb94542a8',
    'node_modules/pako/lib/zlib/adler32.js': '99618eb68a96858148e2dd56f478aa7e4321c434f5f2ee6c57f59c0c568f0596',
    'node_modules/pako/lib/zlib/constants.js': 'e0c4148ed267a27f03aba9f9a34cba4bade2090c0f12905907539e38c842abbc',
    'node_modules/pako/lib/zlib/crc32.js': '213f85e8b2e52e2cf6f7634897197400f7ddaed587274f160045f0b548ec21da',
    'node_modules/pako/lib/zlib/deflate.js': '4b9b71ae7f42e506fe1fb134f1c21b944ef2b7ef79667ba1fe59eb963c353d22',
    'node_modules/pako/lib/zlib/gzheader.js': '8fa0b4995cea477be96b8783a7d6c7cf5fec96765c2108c3bd52e77104bfdbd3',
    'node_modules/pako/lib/zlib/inffast.js': 'b1beb3b55270e8338f23fc397132d179942d5866153ca4e9be264200b00c0d44',
    'node_modules/pako/lib/zlib/inflate.js': 'd31799a23f2ae87f0c52d21241c92278b41d23bb5365b452f2706d41b00c4245',
    'node_modules/pako/lib/zlib/inftrees.js': '75f75d8ae4a7bed86f90a4a38f70c5d95e807515e9326801f9a073a7f2bf8bc7',
    'node_modules/pako/lib/zlib/messages.js': 'eec5f5d6b8120ada9cd48a3ca0a253c64777906e835477b090e053434d38cf6c',
    'node_modules/pako/lib/zlib/trees.js': '718adbbf68f4343bf17d61edffd9d0788a96a93e4e191d857459d95257e614cf',
    'node_modules/pako/lib/zlib/zstream.js': '5fa10c7a59ca99113cf92d1260e56cbd6282e15afa85dc8a0eb8e8d6d2321298',
    'node_modules/pako/LICENSE': 'a04665b3b2de56c66730c1f720f528175739e4104f79073614aa611da1e85539',
    'node_modules/pako/package.json': '0014f350a6834ed6a2d1211e0a2e3aa471cb426edf63379c785f9647ea2b4e01',
    'node_modules/pako/README.md': '8174ca473d9df9da15064d199064e8e3ff88d46beb66b545e048756e6f0fb469',
    'node_modules/pptxgenjs/dist/pptxgen.bundle.js': '4fb9eac5cfefb213e2d8743c2b7151025f31bfb3f834c73c12062916daa0f3f8',
    'node_modules/pptxgenjs/dist/pptxgen.bundle.js.map': '7052233dd27e06d32d9f0431ce98030a64936f74f7f463ff05fea98c4f7fd0e0',
    'node_modules/pptxgenjs/dist/pptxgen.cjs.js': '873d182a8e2e1c0b5e522ef146117936b96b9b2024667bd4c1de59e2b031d27a',
    'node_modules/pptxgenjs/dist/pptxgen.es.js': '05844c5625e2cda3b449eb967c2246dd57ca57341886a7c28eeebca263b29bd4',
    'node_modules/pptxgenjs/dist/pptxgen.min.js': '097f0b92e15035a72bba72b59ef1ece62ab45ec6075ac85fe0e2d80d3f59b8e3',
    'node_modules/pptxgenjs/dist/pptxgen.min.js.map': '3cfaf38a822a934a114fd5b7f0ff59bfffdf90ccde85bb3aa8d61aa69d949ed6',
    'node_modules/pptxgenjs/LICENSE': '7a2bfe96150786ed1908b8e63f98ebab88875c1e79e28faff6649e0f11f77e52',
    'node_modules/pptxgenjs/package.json': 'a4fc694898ef6f7da4c5840b799b593a0a74e9e05b7490b2ac5ee144762db477',
    'node_modules/pptxgenjs/README.md': 'e36118997c58ec08a1fff55e1d9744a47420945167af1efc360af3d1ccaf0d3b',
    'node_modules/pptxgenjs/types/index.d.ts': '0726d015dbcb55ccfa75546cb2fd43fe13a0dfeb783d08572f1c62f59193bbe5',
    'node_modules/process-nextick-args/index.js': '90ba524851f721e8aced79870d6d6a733cd3939b293a83e2d04417812a8ba330',
    'node_modules/process-nextick-args/license.md': 'ecdccbcf39024f624ded480c01c0b25458e1eca8f26ecf040933865ce56d9a4f',
    'node_modules/process-nextick-args/package.json': '6d6d93d057f39bc3173d53e694b61833fd0ce89c1d669156169136d31a968131',
    'node_modules/process-nextick-args/readme.md': 'f659caaae9d9f4a860850b3229f6c261a09b86e3644dbc01128fe5d74b01aecd',
    'node_modules/readable-stream/CONTRIBUTING.md': '56e4e12a6934a2c4d36c7bf893f4d8aefa6c96f9ffcec357dfa6476e36c4f1f5',
    'node_modules/readable-stream/doc/wg-meetings/2015-01-30.md': '39bdf2c2d8d23df3239dde5e66449dcfa9bfd0accf840c91c35bb295f2bbae2d',
    'node_modules/readable-stream/duplex-browser.js': 'b492b180e158a495afa7b394de1440e037c5d60524bb2fce839aeb690e6ff968',
    'node_modules/readable-stream/duplex.js': '16475035143997e924dc3f41af6fd657cf55c5843f415f00b155c20891da8a5b',
    'node_modules/readable-stream/GOVERNANCE.md': '7a521e462d1c6f3b599c44637fb337bbf969dda311510a87236ec539a415331d',
    'node_modules/readable-stream/lib/_stream_duplex.js': 'fd17d6a92dd9ba004c85f8e364b2771af10d012a83766437447dbae63879fa6b',
    'node_modules/readable-stream/lib/_stream_passthrough.js': 'bf61450b1ff5f94fea9d46665e931119642034c903e63cc224b4c96472eed4d4',
    'node_modules/readable-stream/lib/_stream_readable.js': '216e051224eff89a5d5eec76bef25addac078d9ebd2e88bd0a3d73a0e605091d',
    'node_modules/readable-stream/lib/_stream_transform.js': '56220d9dd58b976f1739bfc85948b267d79772ba23672ff402d13b6b3fcf4e40',
    'node_modules/readable-stream/lib/_stream_writable.js': 'e6359ac652ed97f5f328c586c7a6b8f163782a9ca13da476e609a981c75e0469',
    'node_modules/readable-stream/lib/internal/streams/BufferList.js': '387991bfee34bbb7938e0c0a3f345c3e5e4c37d5b0cb600e6d432c9995321fa7',
    'node_modules/readable-stream/lib/internal/streams/destroy.js': 'a59f90daec030125875a6028b32f93e2e2bc9fafd703991dbc36244f5cb21176',
    'node_modules/readable-stream/lib/internal/streams/stream-browser.js': '72d46a15491627d8fb1489a47d03583cfe5c21902918016ab532b53e615e5a9a',
    'node_modules/readable-stream/lib/internal/streams/stream.js': '1e7903927df33aadb3659ecce55266c9c851da65ce6c8b723a60a305c1c5422c',
    'node_modules/readable-stream/LICENSE': 'ec62dc96da0099b87f4511736c87309335527fb7031639493e06c95728dc8c54',
    'node_modules/readable-stream/package.json': '67bde829e31cba3f50c77d14a30fa0f2295223b7ffa07f3b84606a5a79bb97f8',
    'node_modules/readable-stream/passthrough.js': '809dbc03b4c312355ff74eb14b2ccc77267ee71e04f519f437eb4b203407c4b7',
    'node_modules/readable-stream/readable-browser.js': 'ec8e6f4e484d5269bc134752e11770b66b6be3a470217c2a0166e977965f53c6',
    'node_modules/readable-stream/readable.js': 'a1efa3fa06393aff652f3529ea1b1bc32134d49eb794b23272fb0ba13d214550',
    'node_modules/readable-stream/README.md': '5a917d93a11215fbdfe7366e7466bc2ef3e3b9dee8291b0e1d48e6d4796f19cf',
    'node_modules/readable-stream/transform.js': '155f794c5c789568b7bc632cd37f28b9064890e887bfab96a4393100218d4230',
    'node_modules/readable-stream/writable-browser.js': '427bae9a6a026082e46acb500da48b270234ebc2a1ded1315b49eccfa6311c61',
    'node_modules/readable-stream/writable.js': '74cbbdc5d60e1ee6560d2850515b68c3d6b39d9b2f32346aac1386b031c2a661',
    'node_modules/safe-buffer/index.d.ts': '5e379df3d61561c2ed7789b5995b9ba2143bbba21a905e2381e16efe7d1fa424',
    'node_modules/safe-buffer/index.js': 'd58af21cb0518864d0c505742d1af71e5b5e1f142f4c0f27353aa0f431a616d4',
    'node_modules/safe-buffer/LICENSE': 'c7cc929b57080f4b9d0c6cf57669f0463fc5b39906344dfc8d3bc43426b30eac',
    'node_modules/safe-buffer/package.json': '3d8b6d944be9e931a178914afbb3d6b79bfa199c032872b687bed41ed996c747',
    'node_modules/safe-buffer/README.md': 'd301a850808775718ddb510d4bf2a922d9b0afa72894e67137daa20fbeafac05',
    'node_modules/setimmediate/LICENSE.txt': 'c4b4ad3a5746f1f5249a6dd90396ec519264e1bb02e01e48a6522c48a3a97cb4',
    'node_modules/setimmediate/package.json': '56c7968c8062ad56e94b8ddb477e514098e0fa34b060aa86b67232fdb12e184d',
    'node_modules/setimmediate/setImmediate.js': '9dc2eb8ff83f16f42fa5d9721bc15ac5a0b12eca91d185b7840dacf3381b3cd7',
    'node_modules/string_decoder/lib/string_decoder.js': 'f1d36d47b2c579063392c1a68963467f2d4f51a069af09eb068d974c63ee3b37',
    'node_modules/string_decoder/LICENSE': '11f2aafb37d06b3ee5bdaf06e9811141d0da05263c316f3d627f45c20d43261b',
    'node_modules/string_decoder/package.json': 'e278ca18803862a6d1b6bedc6a3394159ac86e5c4a162614fef006c79dcda3a2',
    'node_modules/string_decoder/README.md': '64417fec74bed1e4160c792189c85e6f37f4232a179e2beb04f32d80df76c4cf',
    'node_modules/undici-types/agent.d.ts': '4893a895ea92c85345017a04ed427cbd6a1710453338df26881a6019432febdd',
    'node_modules/undici-types/api.d.ts': '0db18c6e78ea846316c012478888f33c11ffadab9efd1cc8bcc12daded7a60b6',
    'node_modules/undici-types/balanced-pool.d.ts': 'ba7bc87d01492633cb5a0e5da8a4a42a1c86270e7b3d2dea5d156828a84e4882',
    'node_modules/undici-types/cache.d.ts': 'c46e079fe54c76f95c67fb89081b3e399da2c7d109e7dca8e4b58d83e332e605',
    'node_modules/undici-types/client.d.ts': '1d6e127068ea8e104a912e42fc0a110e2aa5a66a356a917a163e8cf9a65e4a75',
    'node_modules/undici-types/connector.d.ts': 'c6fd2c5a395f2432786c9cb8deb870b9b0e8ff7e22c029954fabdd692bff6195',
    'node_modules/undici-types/content-type.d.ts': '2e85db9e6fd73cfa3d7f28e0ab6b55417ea18931423bd47b409a96e4a169e8e6',
    'node_modules/undici-types/cookies.d.ts': '89167d696a849fce5ca508032aabfe901c0868f833a8625d5a9c6e861ef935d2',
    'node_modules/undici-types/diagnostics-channel.d.ts': '4732aec92b20fb28c5fe9ad99521fb59974289ed1e45aecb282616202184064f',
    'node_modules/undici-types/dispatcher.d.ts': '7f182617db458e98fc18dfb272d40aa2fff3a353c44a89b2c0ccb3937709bfb5',
    'node_modules/undici-types/env-http-proxy-agent.d.ts': '24c6a117721e606c9984335f71711877293a9651e44f59f3d21c1ea0856f9cc9',
    'node_modules/undici-types/errors.d.ts': '5ded6427296cdf3b9542de4471d2aa8d3983671d4cac0f4bf9c637208d1ced43',
    'node_modules/undici-types/eventsource.d.ts': '8bd86b8e8f6a6aa6c49b71e14c4ffe1211a0e97c80f08d2c8cc98838006e4b88',
    'node_modules/undici-types/fetch.d.ts': 'c464d66b20788266e5353b48dc4aa6bc0dc4a707276df1e7152ab0c9ae21fad8',
    'node_modules/undici-types/file.d.ts': '25c8056edf4314820382a5fdb4bb7816999acdcb929c8f75e3f39473b87e85bc',
    'node_modules/undici-types/filereader.d.ts': '317e63deeb21ac07f3992f5b50cdca8338f10acd4fbb7257ebf56735bf52ab00',
    'node_modules/undici-types/formdata.d.ts': '78d0d27c130d35c60b5e5566c9f1e5be77caf39804636bc1a40133919a949f21',
    'node_modules/undici-types/global-dispatcher.d.ts': 'cadc8aced301244057c4e7e73fbcae534b0f5b12a37b150d80e5a45aa4bebcbd',
    'node_modules/undici-types/global-origin.d.ts': '385aab901643aa54e1c36f5ef3107913b10d1b5bb8cbcd933d4263b80a0d7f20',
    'node_modules/undici-types/handlers.d.ts': '11396ed8a44c02ab9798b7dca436009f866e8dae3c9c25e8c1fbc396880bf1bb',
    'node_modules/undici-types/header.d.ts': '5929864ce17fba74232584d90cb721a89b7ad277220627cc97054ba15a98ea8f',
    'node_modules/undici-types/index.d.ts': 'bf67d53d168abc1298888693338cb82854bdb2e69ef83f8a0092093c2d562107',
    'node_modules/undici-types/interceptors.d.ts': 'e61be3f894b41b7baa1fbd6a66893f2579bfad01d208b4ff61daef21493ef0a8',
    'node_modules/undici-types/LICENSE': 'a6db8096b2707bc0102d256917d4d33f298ba36d8c3f25de067a2b5bb379db27',
    'node_modules/undici-types/mock-agent.d.ts': '13f6f39e12b1518c6650bbb220c8985999020fe0f21d818e28f512b7771d00f9',
    'node_modules/undici-types/mock-client.d.ts': '9b5369969f6e7175740bf51223112ff209f94ba43ecd3bb09eefff9fd675624a',
    'node_modules/undici-types/mock-errors.d.ts': '24515859bc0b836719105bb6cc3d68255042a9f02a6022b3187948b204946bd2',
    'node_modules/undici-types/mock-interceptor.d.ts': 'c21dc52e277bcfc75fac0436ccb75c204f9e1b3fa5e12729670910639f27343e',
    'node_modules/undici-types/mock-pool.d.ts': '4fe9e626e7164748e8769bbf74b538e09607f07ed17c2f20af8d680ee49fc1da',
    'node_modules/undici-types/package.json': '11f873b423b96a5ad444a099685ca6b9de1379dcd83fd8d368e757ddb53658e4',
    'node_modules/undici-types/patch.d.ts': '615ba88d0128ed16bf83ef8ccbb6aff05c3ee2db1cc0f89ab50a4939bfc1943f',
    'node_modules/undici-types/pool-stats.d.ts': '9670d44354bab9d9982eca21945686b5c24a3f893db73c0dae0fd74217a4c219',
    'node_modules/undici-types/pool.d.ts': '0b8a9268adaf4da35e7fa830c8981cfa22adbbe5b3f6f5ab91f6658899e657a7',
    'node_modules/undici-types/proxy-agent.d.ts': 'ea0148f897b45a76544ae179784c95af1bd6721b8610af9ffa467a518a086a43',
    'node_modules/undici-types/readable.d.ts': '763fe0f42b3d79b440a9b6e51e9ba3f3f91352469c1e4b3b67bfa4ff6352f3f4',
    'node_modules/undici-types/README.md': 'c8cced6ec588c55b07e35733258cb7d6b63e3426ee7490aa9267aab4bda0fb20',
    'node_modules/undici-types/retry-agent.d.ts': '405822be75ad3e4d162e07439bac80c6bcc6dbae1929e179cf467ec0b9ee4e2e',
    'node_modules/undici-types/retry-handler.d.ts': 'dd3273ead9fbde62a72949c97dbec2247ea08e0c6952e701a483d74ef92d6a17',
    'node_modules/undici-types/util.d.ts': 'bd0532fd6556073727d28da0edfd1736417a3f9f394877b6d5ef6ad88fba1d1a',
    'node_modules/undici-types/webidl.d.ts': '99c42860a78963ee66048ef3536dbd9eb59858e6a06f2feba845105ed411e658',
    'node_modules/undici-types/websocket.d.ts': 'a4d551dbf8746780194d550c88f26cf937caf8d56f102969a110cfaed4b06656',
    'node_modules/util-deprecate/browser.js': 'abaef53ebcdc98f3655331f834986a2d11b6a824f58d670369669246d1b7b69b',
    'node_modules/util-deprecate/History.md': 'ab183ff93d075137547f9d96938185f2cd4b29ba1f4e28b29e6fbd573255968d',
    'node_modules/util-deprecate/LICENSE': '0154425673db15cdfa80ecba2c9b1f1a867f7197a006764712849bfc3a93cbb7',
    'node_modules/util-deprecate/node.js': '9a86a29fa34a99b861e707345fb1d1e2e55a6c23edb8f992bed57cc607f42d8e',
    'node_modules/util-deprecate/package.json': '7bba467f049074957e693fc06672848b040c38fa071b6eed8690f5fbe090a8b2',
    'node_modules/util-deprecate/README.md': 'd30a5eca847bc6edc7b9723884def8942a66f89d12a5735dc22d1ee1216ff2ea',
}


def _sha(contents: bytes) -> str:
    return hashlib.sha256(contents).hexdigest()


def _json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _allowed_name(name: str) -> bool:
    if name in TOP_LEVEL_FILES or name == "references/scene.md":
        return True
    parts = PurePosixPath(name).parts
    if len(parts) == 2 and parts[0] == "newpath" and not parts[1].startswith("."):
        return Path(parts[1]).suffix in (".py", ".cjs")
    return name in LOCKED_DEPENDENCY_FILES


def _safe_member_name(name: str) -> bool:
    return (isinstance(name, str) and bool(name) and not any(c in name for c in "\\:\x00")
            and not name.startswith("/") and all(part not in ("", ".", "..") for part in name.split("/")))


def _safe_location(root: Path, relative: str) -> Path:
    if not _safe_member_name(relative):
        raise ValueError(f"Unsafe source member name: {relative}")
    path = root.joinpath(*relative.split("/"))
    current = root
    for part in relative.split("/"):
        current /= part
        try:
            details = current.lstat()
        except OSError as exc:
            raise ValueError(f"Required package input is missing: {relative}") from exc
        if stat.S_ISLNK(details.st_mode) or getattr(details, "st_file_attributes", 0) & 0x400:
            raise ValueError(f"Source links/reparse points are forbidden: {relative}")
    if not path.resolve().is_relative_to(root):
        raise ValueError(f"Source resolves outside the package root: {relative}")
    return path


def _safe_source(root: Path, relative: str) -> Path:
    path = _safe_location(root, relative)
    if not path.is_file():
        raise ValueError(f"Required package input is not a file: {relative}")
    return path


def _select_sources(root: Path) -> list[str]:
    selected = set(TOP_LEVEL_FILES) | {"references/scene.md"} | set(LOCKED_DEPENDENCY_FILES)
    module_dir = _safe_location(root, "newpath")
    if not module_dir.is_dir():
        raise ValueError("Required source directory is missing: newpath")
    for path in module_dir.iterdir():
        relative = "newpath/" + path.name
        if _allowed_name(relative):
            selected.add(relative)
    if not any(name.startswith("newpath/") for name in selected):
        raise ValueError("No declared newpath Python/JavaScript sources exist")
    return sorted(selected)


def _snapshot(root: Path):
    payloads = {}
    entries = []
    for relative in _select_sources(root):
        path = _safe_source(root, relative)
        before = path.stat()
        contents = path.read_bytes()
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise ValueError(f"Source changed while being read: {relative}")
        digest = _sha(contents)
        expected = LOCKED_DEPENDENCY_FILES.get(relative)
        if expected is not None and expected != digest:
            raise ValueError(f"Locked dependency hash changed: {relative}")
        payloads[relative] = contents
        entries.append({"path": relative, "size": len(contents), "sha256": digest})
    return payloads, entries


def _load_json(contents: bytes, label: str):
    try:
        value = json.loads(contents.decode("utf-8-sig"))
    except (ValueError, UnicodeError) as exc:
        raise ValueError(f"Invalid JSON in {label}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {label}")
    return value


def _validate_dependencies(payloads):
    package = _load_json(payloads["package.json"], "package.json")
    lock = _load_json(payloads["package-lock.json"], "package-lock.json")
    if package.get("dependencies") != ROOT_VERSIONS:
        raise ValueError("Production dependencies must pin pptxgenjs 4.0.1 and jszip 3.10.1 exactly")
    packages = lock.get("packages")
    if lock.get("lockfileVersion") != 3 or not isinstance(packages, dict):
        raise ValueError("Production dependency lock must use lockfileVersion 3")
    if not isinstance(packages.get(""), dict) or packages[""].get("dependencies") != ROOT_VERSIONS:
        raise ValueError("Root lock dependencies disagree with the pinned production versions")
    for name, version in ROOT_VERSIONS.items():
        if not isinstance(packages.get("node_modules/" + name), dict) or packages["node_modules/" + name].get("version") != version:
            raise ValueError(f"Locked production dependency version is wrong: {name}")
    roots = []
    for relative, metadata in packages.items():
        if not relative:
            continue
        if not _safe_member_name(relative) or not relative.startswith("node_modules/") or not isinstance(metadata, dict):
            raise ValueError("Unsafe or invalid dependency lock entry")
        package_path = relative + "/package.json"
        if package_path not in LOCKED_DEPENDENCY_FILES or package_path not in payloads:
            raise ValueError(f"Lock entry has no frozen local dependency: {relative}")
        installed = _load_json(payloads[package_path], package_path)
        if installed.get("version") != metadata.get("version"):
            raise ValueError(f"Installed dependency version disagrees with lock: {relative}")
        roots.append(relative + "/")
    if any(not any(name.startswith(root) for root in roots) for name in LOCKED_DEPENDENCY_FILES):
        raise ValueError("Frozen dependency has no declared production lock entry")


def _write_archive(path: Path, payloads: dict[str, bytes], manifest_bytes: bytes):
    with ZipFile(path, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for name, contents in sorted({**payloads, MANIFEST_NAME: manifest_bytes}.items()):
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, contents, compress_type=ZIP_DEFLATED, compresslevel=9)


def verify_package(path: Path, *, expected_manifest_sha256: str | None = None) -> dict:
    """Verify CRC, exact declared members, per-file SHA and optional manifest SHA.

    This checks package integrity and the frozen dependency inventory. A hash
    manifest is not a digital signature or a claim of runtime/visual acceptance.
    """
    path = Path(path)
    try:
        with ZipFile(path) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise ValueError("ZIP contains duplicate members")
            if any(not _safe_member_name(name) for name in names):
                raise ValueError("ZIP contains an unsafe member path")
            if archive.testzip() is not None:
                raise ValueError("ZIP CRC validation failed")
            if MANIFEST_NAME not in names:
                raise ValueError("ZIP manifest member is missing")
            manifest_bytes = archive.read(MANIFEST_NAME)
            manifest_sha = _sha(manifest_bytes)
            if expected_manifest_sha256 is not None and manifest_sha != expected_manifest_sha256:
                raise ValueError("Package manifest SHA-256 does not match the expected value")
            manifest = _load_json(manifest_bytes, MANIFEST_NAME)
            if manifest.get("format") != MANIFEST_FORMAT or manifest.get("archive_name") != ARCHIVE_NAME:
                raise ValueError("Invalid package manifest format or archive name")
            if manifest.get("dependency_versions") != ROOT_VERSIONS:
                raise ValueError("Invalid manifest dependency versions")
            entries = manifest.get("files")
            if not isinstance(entries, list) or not entries:
                raise ValueError("Invalid manifest file entries")
            declared = []
            dependency_count = 0
            for entry in entries:
                if not isinstance(entry, dict) or set(entry) != {"path", "size", "sha256"}:
                    raise ValueError("Invalid manifest member declaration")
                name, size, digest = entry["path"], entry["size"], entry["sha256"]
                if not _safe_member_name(name) or not _allowed_name(name):
                    raise ValueError("Manifest member is outside the production whitelist")
                if isinstance(size, bool) or not isinstance(size, int) or size < 0 or not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
                    raise ValueError("Invalid manifest size or SHA-256")
                declared.append(name)
                if name in LOCKED_DEPENDENCY_FILES:
                    dependency_count += 1
                    if digest != LOCKED_DEPENDENCY_FILES[name]:
                        raise ValueError(f"Locked dependency SHA-256 disagrees: {name}")
            if len(declared) != len(set(declared)) or set(names) != {*declared, MANIFEST_NAME}:
                raise ValueError("ZIP members do not match the declared manifest members")
            required = set(TOP_LEVEL_FILES) | {"references/scene.md"} | set(LOCKED_DEPENDENCY_FILES)
            if not required.issubset(declared) or not any(name.startswith("newpath/") for name in declared):
                raise ValueError("Required production manifest members are missing")
            if entries != sorted(entries, key=lambda entry: entry["path"]):
                raise ValueError("Manifest members must use canonical sorted order")
            if manifest.get("source_file_count") != len(entries) or manifest.get("dependency_file_count") != dependency_count or dependency_count != len(LOCKED_DEPENDENCY_FILES):
                raise ValueError("Manifest file counts disagree with declared members")
            snapshot_sha = _sha(_json_bytes(entries))
            if manifest.get("source_snapshot_sha256") != snapshot_sha:
                raise ValueError("Manifest source snapshot SHA-256 is inconsistent")
            payloads = {}
            for entry in entries:
                contents = archive.read(entry["path"])
                if len(contents) != entry["size"] or _sha(contents) != entry["sha256"]:
                    raise ValueError(f"Package member size or SHA-256 differs: {entry['path']}")
                payloads[entry["path"]] = contents
            _validate_dependencies(payloads)
    except (BadZipFile, KeyError, OSError, zlib.error) as exc:
        raise ValueError("ZIP CRC or member validation failed") from exc
    return {"zip_crc_valid": True, "manifest_valid": True, "manifest_sha256": manifest_sha,
            "source_snapshot_sha256": snapshot_sha, "source_file_count": len(entries),
            "dependency_file_count": dependency_count, "dependency_versions": dict(ROOT_VERSIONS)}


def freeze_source(root: Path) -> dict:
    """Return a serializable production source identity before tests run.

    Passing this object as tested_snapshot later binds packaging to these exact
    paths, byte sizes and SHA-256 values. This function does not run tests or
    create a production archive. Tests/scripts may be frozen separately.
    """
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError("Explicit package source root does not exist")
    payloads, entries = _snapshot(root)
    _validate_dependencies(payloads)
    return {"format": "newpath.source-snapshot/1", "files": entries,
            "source_snapshot_sha256": _sha(_json_bytes(entries)),
            "dependency_versions": dict(ROOT_VERSIONS)}


def build_package(root: Path, *, output_dir: Path | None = None, tested_snapshot: dict | None = None) -> dict:
    """Stage an explicit source root, verify it stayed unchanged, then publish.

    Existing archives are never overwritten. Excluded files/directories are
    neither copied nor imported, and no installation or download is attempted.
    """
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError("Explicit package source root does not exist")
    destination = Path(output_dir).resolve() if output_dir is not None else root / "dist"
    output = destination / ARCHIVE_NAME
    if output.exists():
        raise FileExistsError(f"Package output already exists: {output}")
    payloads, before = _snapshot(root)
    _validate_dependencies(payloads)
    if tested_snapshot is not None:
        expected = {"format": "newpath.source-snapshot/1", "files": before,
                    "source_snapshot_sha256": _sha(_json_bytes(before)),
                    "dependency_versions": dict(ROOT_VERSIONS)}
        if tested_snapshot != expected:
            raise ValueError("Production source differs from the tested snapshot; no archive was built")
    manifest = {"format": MANIFEST_FORMAT, "archive_name": ARCHIVE_NAME,
                "dependency_versions": dict(ROOT_VERSIONS),
                "dependency_file_count": len(LOCKED_DEPENDENCY_FILES),
                "source_file_count": len(before), "files": before,
                "source_snapshot_sha256": _sha(_json_bytes(before))}
    manifest_bytes = _json_bytes(manifest)
    destination.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".newpath-package-", suffix=".zip", dir=destination)
    os.close(descriptor)
    temporary = Path(temporary)
    try:
        _write_archive(temporary, payloads, manifest_bytes)
        checked = verify_package(temporary, expected_manifest_sha256=_sha(manifest_bytes))
        _, after = _snapshot(root)
        if before != after:
            raise ValueError("Source changed during packaging; final archive was not published")
        archive_sha = _sha(temporary.read_bytes())
        report = {"archive": str(output), "sha256": archive_sha, **checked, "source_unchanged": True,
                  "tested_snapshot_verified": tested_snapshot is not None}
        if os.name == "nt":
            os.rename(temporary, output)
        else:
            os.link(temporary, output)
            temporary.unlink()
        return report
    finally:
        if temporary.exists():
            temporary.unlink()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build or verify the independent production allowlist ZIP offline")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="Explicit frozen source root")
    parser.add_argument("--output-dir", type=Path, help="Default: <root>/dist")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--verify", type=Path, metavar="ZIP", help="Verify an existing archive instead of building")
    actions.add_argument("--freeze-source", type=Path, metavar="JSON", help="Write a new production snapshot before tests; do not build")
    parser.add_argument("--tested-snapshot", type=Path, metavar="JSON", help="Require the exact previously frozen/tested source identity")
    parser.add_argument("--expected-manifest-sha256", help="Optional machine-provided manifest SHA for --verify")
    arguments = parser.parse_args(argv)
    try:
        if arguments.verify is not None:
            report = verify_package(arguments.verify, expected_manifest_sha256=arguments.expected_manifest_sha256)
        elif arguments.freeze_source is not None:
            report = freeze_source(arguments.root)
            with arguments.freeze_source.open("xb") as snapshot_file:
                snapshot_file.write(_json_bytes(report))
        else:
            snapshot = _load_json(arguments.tested_snapshot.read_bytes(), "tested snapshot") if arguments.tested_snapshot is not None else None
            report = build_package(arguments.root, output_dir=arguments.output_dir, tested_snapshot=snapshot)
    except (ValueError, FileExistsError, OSError) as exc:
        print(f"Package error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
