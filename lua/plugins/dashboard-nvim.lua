return {
  "nvimdev/dashboard-nvim",
  lazy = false, -- As https://github.com/nvimdev/dashboard-nvim/pull/450, dashboard-nvim shouldn't be lazy-loaded to properly handle stdin.
  opts = function()
    local logo = [[
                                                                                                                                              
    I8,        8        ,8I                         88            88        88                                   88                          
    `8b       d8b       d8'                         88            88        88                                   88                          
     "8,     ,8"8,     ,8"                          88            88        88                                   88                          
      Y8     8P Y8     8P   ,adPPYba,   8b,dPPYba,  88   ,d8      88aaaaaaaa88  ,adPPYYba,  8b,dPPYba,   ,adPPYb,88   ,adPPYba,  8b,dPPYba,  
      `8b   d8' `8b   d8'  a8"     "8a  88P'   "Y8  88 ,a8"       88""""""""88  ""     `Y8  88P'   "Y8  a8"    `Y88  a8P_____88  88P'   "Y8  
       `8a a8'   `8a a8'   8b       d8  88          8888[         88        88  ,adPPPPP88  88          8b       88  8PP"""""""  88          
        `8a8'     `8a8'    "8a,   ,a8"  88          88`"Yba,      88        88  88,    ,88  88          "8a,   ,d88  "8b,   ,aa  88          
         `8'       `8'      `"YbbdP"'   88          88   `Y8a     88        88  `"8bbdP"Y8  88           `"8bbdP"Y8   `"Ybbd8"'  88          
                                                                                                                                         
                                                                                                                                        
     ]]

    logo = string.rep("\n", 8) .. logo .. "\n\n"

    local opts = {
      theme = "doom",
      hide = {
        -- this is taken care of by lualine
        -- enabling this messes up the actual laststatus setting after loading a file
        statusline = false,
      },
      config = {
        header = vim.split(logo, "\n"),
        -- stylua: ignore
        center = {
          { action = 'lua LazyVim.pick()()',                           desc = " Find File",       icon = " ", key = "f" },
          { action = "ene | startinsert",                              desc = " New File",        icon = " ", key = "n" },
          { action = 'lua LazyVim.pick("oldfiles")()',                 desc = " Recent Files",    icon = " ", key = "r" },
          { action = 'lua LazyVim.pick("live_grep")()',                desc = " Find Text",       icon = " ", key = "g" },
          { action = 'lua LazyVim.pick.config_files()()',              desc = " Config",          icon = " ", key = "c" },
          { action = 'lua require("persistence").load()',              desc = " Restore Session", icon = " ", key = "s" },
          { action = "LazyExtras",                                     desc = " Lazy Extras",     icon = " ", key = "x" },
          { action = "Lazy",                                           desc = " Lazy",            icon = "󰒲 ", key = "l" },
          { action = function() vim.api.nvim_input("<cmd>qa<cr>") end, desc = " Quit",            icon = " ", key = "q" },
        },
        footer = function()
          local stats = require("lazy").stats()
          local ms = (math.floor(stats.startuptime * 100 + 0.5) / 100)
          return { "⚡ Neovim loaded " .. stats.loaded .. "/" .. stats.count .. " plugins in " .. ms .. "ms" }
        end,
      },
    }

    for _, button in ipairs(opts.config.center) do
      button.desc = button.desc .. string.rep(" ", 43 - #button.desc)
      button.key_format = "  %s"
    end

    -- open dashboard after closing lazy
    if vim.o.filetype == "lazy" then
      vim.api.nvim_create_autocmd("WinClosed", {
        pattern = tostring(vim.api.nvim_get_current_win()),
        once = true,
        callback = function()
          vim.schedule(function()
            vim.api.nvim_exec_autocmds("UIEnter", { group = "dashboard" })
          end)
        end,
      })
    end

    return opts
  end,
}
