"""上游切图算法（逐字复制自 yedict-data/tools/，行为保持不变）。

* ``zihai_grid`` / ``zihai_crop``    《中华字海》版面网格 → 字头
* ``hzh_*``                          《汉字海》字头定位、字段、编号、补切、复核
* ``kx_locate`` / ``kx_pagemap`` / ``build_kx_sprites``   康熙内府本定位与雪碧图
* ``add_sprites`` / ``build_u_sprites`` / ``build_t_sprites`` / ``chart_pdf_to_paths``
                                     字形入雪碧图与字体/代码表取轮廓

这些文件只做参考与调用，不改逻辑；对外统一由 ``app.pipelines.*`` 包装。
"""
